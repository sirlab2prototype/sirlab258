#!/usr/bin/env python3

import math
import time

import numpy as np
import rclpy

from rclpy.node import Node
from sensor_msgs.msg import Joy, JointState
from indy_interfaces.srv import IndyService
from std_srvs.srv import Trigger

from neuromeka import IndyDCP3

from .qp_core import (
    CartesianFilter,
    JOINT_MAX_DEG,
    JOINT_MIN_DEG,
    qp_step,
    shape_axis,
    shape_trigger,
)


TELE_JOINT_RELATIVE = 11

MSG_RECOVER = 1
MSG_MOVE_HOME = 2
MSG_MOVE_ZERO = 3
MSG_TELE_JOINT_RLT = 7
MSG_TELE_STOP = 8


class QPTeleop(Node):
    def __init__(self):
        super().__init__('qp_teleop')

        self.declare_parameter('indy_ip', '192.168.1.10')
        self.declare_parameter('control_hz', 30.0)
        self.declare_parameter('linear_step_mm', 5.0)
        self.declare_parameter('angular_speed_rad_s', 0.3)
        self.declare_parameter('joint_speed_deg_s', 45.0)
        self.declare_parameter('soft_margin_deg', 15.0)
        self.declare_parameter('vel_ratio', 0.8)
        self.declare_parameter('acc_ratio', 1.0)

        self.indy_ip = str(self.get_parameter('indy_ip').value)
        self.hz = float(self.get_parameter('control_hz').value)
        self.linear_step = float(
            self.get_parameter('linear_step_mm').value
        )
        self.angular_speed = float(
            self.get_parameter('angular_speed_rad_s').value
        )
        self.joint_speed = float(
            self.get_parameter('joint_speed_deg_s').value
        )
        self.soft_margin = float(
            self.get_parameter('soft_margin_deg').value
        )
        self.vel_ratio = float(
            self.get_parameter('vel_ratio').value
        )
        self.acc_ratio = float(
            self.get_parameter('acc_ratio').value
        )

        self.latest_joy = None
        self.last_joy_time = None

        self.joint_deg = None
        self.last_joint_time = None

        self.armed = False
        self.last_share = 0
        self.last_a = 0
        self.last_b = 0

        self.origin_joint = None
        self.relative_joint = np.zeros(6)

        self.cart_filter = CartesianFilter(
            control_hz=self.hz,
            acceleration_mm_s2=300.0,
            alpha=0.35,
        )

        self.indy = IndyDCP3(self.indy_ip)

        self.declare_parameter(
            'capstone_home_joint_deg',
            [
                0.005221,
                40.003174,
                -129.996640,
                90.000870,
                0.000435,
                90.507,
            ],
        )
        self.declare_parameter(
            'capstone_home_vel_ratio',
            10.0,
        )
        self.declare_parameter(
            'capstone_home_acc_ratio',
            10.0,
        )

        self.capstone_home_joint_deg = [
            float(v)
            for v in self.get_parameter(
                'capstone_home_joint_deg'
            ).value
        ]

        if len(self.capstone_home_joint_deg) != 6:
            raise ValueError(
                'capstone_home_joint_deg must contain 6 values'
            )

        self.capstone_home_vel_ratio = float(
            self.get_parameter(
                'capstone_home_vel_ratio'
            ).value
        )
        self.capstone_home_acc_ratio = float(
            self.get_parameter(
                'capstone_home_acc_ratio'
            ).value
        )

        self.indy_service_client = self.create_client(
            IndyService,
            '/indy_srv',
        )

        self.gripper_grasp_client = self.create_client(
            Trigger,
            '/gripper/grasp',
        )

        self.gripper_open_client = self.create_client(
            Trigger,
            '/gripper/open',
        )
        self.command_future = None
        self.mode_future = None
        self.arming = False
        self.waiting_for_fresh_joint = False
        self.arm_token = 0

        self.create_subscription(
            Joy,
            '/joy',
            self.joy_callback,
            10,
        )

        self.create_subscription(
            JointState,
            '/joint_states',
            self.joint_callback,
            10,
        )

        self.timer = self.create_timer(
            1.0 / self.hz,
            self.control_loop,
        )

        self.get_logger().info(
            'QP teleop ready | '
            'Share = ARM/DISARM | '
            'RT/LT=X, right stick=Y/Z | '
            'left stick=Rx/Ry, LB/RB=Rz'
        )

    def joint_callback(self, msg):
        lookup = {
            name: position
            for name, position in zip(msg.name, msg.position)
        }

        names = [
            'joint0',
            'joint1',
            'joint2',
            'joint3',
            'joint4',
            'joint5',
        ]

        if not all(name in lookup for name in names):
            return

        self.joint_deg = np.array([
            math.degrees(lookup[name])
            for name in names
        ])

        self.last_joint_time = time.monotonic()

        # /indy_srv enters TELE_OP synchronously.
        # Joint-state publication can pause during that transition.
        # Finish ARM only after a fresh state arrives again.
        if self.arming and self.waiting_for_fresh_joint:
            self.origin_joint = self.joint_deg.copy()
            self.relative_joint.fill(0.0)
            self.cart_filter.reset()

            self.last_joy_time = time.monotonic()

            self.waiting_for_fresh_joint = False
            self.arming = False
            self.armed = True

            self.get_logger().warning(
                'QP TELEOP ARMED'
            )

    def joy_callback(self, msg):
        if len(msg.axes) < 8 or len(msg.buttons) < 12:
            return

        self.latest_joy = msg
        self.last_joy_time = time.monotonic()

        share = int(msg.buttons[11])
        view = int(msg.buttons[6])
        a = int(msg.buttons[0])
        b = int(msg.buttons[1])
        y = int(msg.buttons[3])

        # View acts as a safety modifier.
        # Pressing it immediately disables active teleop.
        if view and not getattr(self, 'last_view', 0):
            if self.armed or self.arming:
                self.disarm('view button')
            self.view_combo_used = False

        # System commands are triggered only while View is held.
        if view and not getattr(self, 'view_combo_used', False):
            if a:
                self.view_combo_used = True
                self.request_robot_command(
                    MSG_RECOVER,
                    'Recover',
                )
            elif b:
                self.view_combo_used = True
                self.request_robot_command(
                    MSG_MOVE_ZERO,
                    'Zero',
                )
            elif y:
                self.view_combo_used = True
                self.request_capstone_home()

        # Share remains the normal QP ARM/DISARM button.
        if not view and share and not self.last_share:
            if self.armed:
                self.disarm('operator')
            else:
                self.arm()

        # Normal A/B control the Mark7 gripper.
        # View combinations above take priority.
        if not view:
            if a and not self.last_a:
                self.request_gripper(
                    self.gripper_grasp_client,
                    'Grasp',
                )

            if b and not self.last_b:
                self.request_gripper(
                    self.gripper_open_client,
                    'Open',
                )

        self.last_share = share
        self.last_view = view
        self.last_a = a
        self.last_b = b

    def request_gripper(self, client, label):
        if not client.service_is_ready():
            self.get_logger().error(
                f'Gripper service unavailable: {label}'
            )
            return

        request = Trigger.Request()
        future = client.call_async(request)

        future.add_done_callback(
            lambda f, name=label:
                self.gripper_command_done(f, name)
        )

    def gripper_command_done(self, future, label):
        try:
            response = future.result()

            if response.success:
                self.get_logger().info(
                    f'Gripper {label}: {response.message}'
                )
            else:
                self.get_logger().error(
                    f'Gripper {label} failed: '
                    f'{response.message}'
                )

        except Exception as exc:
            self.get_logger().error(
                f'Gripper {label} service failed: {exc}'
            )

    def request_capstone_home(self):
        if self.armed or self.arming:
            self.disarm('capstone home')

        if not self.indy_service_client.service_is_ready():
            self.get_logger().error(
                'Cannot move HOME: /indy_srv unavailable'
            )
            return

        if (
            self.command_future is not None
            and not self.command_future.done()
        ):
            self.get_logger().warning(
                'Robot command already in progress'
            )
            return

        # First make sure TELE_OP has fully stopped.
        request = IndyService.Request()
        request.data = MSG_TELE_STOP

        self.get_logger().warning(
            'Preparing CAPSTONE HOME...'
        )

        self.command_future = (
            self.indy_service_client.call_async(request)
        )
        self.command_future.add_done_callback(
            self.capstone_home_after_stop
        )

    def capstone_home_after_stop(self, future):
        try:
            response = future.result()

            if not response.success:
                self.get_logger().error(
                    'Could not stop TELE_OP before HOME'
                )
                return

            motion = self.indy.get_motion_data()

            if motion.get('is_in_motion', False):
                self.get_logger().error(
                    'Cannot move HOME: robot still moving'
                )
                return

            self.get_logger().warning(
                'Moving to CAPSTONE HOME: '
                f'{self.capstone_home_joint_deg}'
            )

            self.indy.movej(
                jtarget=self.capstone_home_joint_deg,
                vel_ratio=self.capstone_home_vel_ratio,
                acc_ratio=self.capstone_home_acc_ratio,
            )

        except Exception as exc:
            self.get_logger().error(
                f'CAPSTONE HOME failed: {exc}'
            )

    def request_robot_command(self, data, label):
        if (
            self.command_future is not None
            and not self.command_future.done()
        ):
            self.get_logger().warning(
                'Robot command already in progress'
            )
            return

        if not self.indy_service_client.service_is_ready():
            self.get_logger().error(
                '/indy_srv is not available'
            )
            return

        request = IndyService.Request()
        request.data = data

        self.get_logger().warning(
            f'Requesting {label}'
        )

        self.command_future = (
            self.indy_service_client.call_async(request)
        )

        self.command_future.add_done_callback(
            lambda future, command=label:
                self.robot_command_done(
                    future,
                    command,
                )
        )

    def robot_command_done(self, future, label):
        try:
            response = future.result()

            if response.success:
                self.get_logger().warning(
                    f'{label} command accepted'
                )
            else:
                self.get_logger().error(
                    f'{label} failed: {response.message}'
                )

        except Exception as exc:
            self.get_logger().error(
                f'{label} service failed: {exc}'
            )

    def arm(self):
        if self.armed or self.arming:
            return

        if self.joint_deg is None:
            self.get_logger().error(
                'Cannot arm: no /joint_states yet'
            )
            return

        if (
            self.last_joint_time is None
            or time.monotonic() - self.last_joint_time > 0.5
        ):
            self.get_logger().error(
                'Cannot arm: stale /joint_states'
            )
            return

        if not self.indy_service_client.service_is_ready():
            self.get_logger().error(
                'Cannot arm: /indy_srv is not available'
            )
            return

        try:
            self.indy.set_teleop_params(
                smooth_factor=0.5,
                cutoff_freq=8.0,
                error_gain=10.0,
            )
        except Exception as exc:
            self.get_logger().error(
                f'Could not set teleop parameters: {exc}'
            )
            return

        self.arming = True
        self.arm_token += 1
        token = self.arm_token

        self.cart_filter.reset()
        self.relative_joint.fill(0.0)

        request = IndyService.Request()
        request.data = MSG_TELE_JOINT_RLT

        self.get_logger().warning(
            'QP TELEOP ARMING...'
        )

        self.mode_future = (
            self.indy_service_client.call_async(request)
        )

        self.mode_future.add_done_callback(
            lambda future, t=token:
                self.arm_done(future, t)
        )

    def arm_done(self, future, token):
        if token != self.arm_token:
            return

        try:
            response = future.result()

            if not response.success:
                self.arming = False
                self.get_logger().error(
                    f'Could not enter joint-relative TELE_OP: '
                    f'{response.message}'
                )
                return

        except Exception as exc:
            self.arming = False
            self.get_logger().error(
                f'Teleop start service failed: {exc}'
            )
            return

        # Do NOT check joint-state freshness here.
        # The official driver may pause its joint-state timer while
        # servicing the TELE_OP transition.
        #
        # joint_callback() will complete ARM as soon as the next
        # fresh robot state arrives.
        self.waiting_for_fresh_joint = True

        self.get_logger().info(
            'TELE_OP ready; waiting for fresh /joint_states'
        )

    def disarm(self, reason):
        if not self.armed and not self.arming:
            return

        # Invalidate any pending ARM callback immediately.
        self.arm_token += 1
        self.arming = False
        self.waiting_for_fresh_joint = False
        self.armed = False

        self.cart_filter.reset()
        self.relative_joint.fill(0.0)

        self.get_logger().warning(
            f'QP TELEOP DISARMED: {reason}'
        )

        if self.indy_service_client.service_is_ready():
            request = IndyService.Request()
            request.data = MSG_TELE_STOP

            self.mode_future = (
                self.indy_service_client.call_async(request)
            )
        else:
            # Safety fallback if ROS service is unavailable.
            try:
                self.indy.stop_teleop()
            except Exception as exc:
                self.get_logger().error(
                    f'stop_teleop fallback failed: {exc}'
                )

    def control_loop(self):
        if not self.armed:
            return

        now = time.monotonic()

        if (
            self.latest_joy is None
            or self.last_joy_time is None
            or now - self.last_joy_time > 0.5
        ):
            self.disarm('joy timeout')
            return

        if (
            self.joint_deg is None
            or self.last_joint_time is None
            or now - self.last_joint_time > 0.5
        ):
            self.disarm('joint-state timeout')
            return

        joy = self.latest_joy

        # Current Xbox mapping:
        # LT/RT = X
        # right stick X = Y
        # right stick Y = Z
        lt = shape_trigger(joy.axes[2])
        rt = shape_trigger(joy.axes[5])

        right_x = shape_axis(joy.axes[3])
        right_y = shape_axis(joy.axes[4])

        left_x = shape_axis(joy.axes[0])
        left_y = shape_axis(joy.axes[1])

        lb = float(joy.buttons[4])
        rb = float(joy.buttons[5])

        raw_translation = np.array([
            (rt - lt) * self.linear_step,
            right_x * self.linear_step,
            right_y * self.linear_step,
        ])

        translation = self.cart_filter.update(
            raw_translation
        )

        angular_step = self.angular_speed / self.hz

        rotation = np.array([
            left_x * angular_step,
            left_y * angular_step,
            (rb - lb) * angular_step,
        ])

        if (
            np.linalg.norm(translation) < 1e-6
            and np.linalg.norm(rotation) < 1e-6
        ):
            return

        commanded_joint = (
            self.origin_joint + self.relative_joint
        )

        try:
            step = qp_step(
                commanded_joint,
                translation,
                rotation_rad=rotation,
                control_hz=self.hz,
                soft_margin_deg=self.soft_margin,
                max_joint_speed_deg_s=self.joint_speed,
            )

        except Exception as exc:
            self.get_logger().error(
                f'QP failed: {exc}'
            )
            self.disarm('QP error')
            return

        candidate_relative = (
            self.relative_joint + step
        )

        candidate_joint = (
            self.origin_joint + candidate_relative
        )

        # Final hard sanity check before sending.
        if np.any(candidate_joint < JOINT_MIN_DEG):
            self.disarm('joint minimum guard')
            return

        if np.any(candidate_joint > JOINT_MAX_DEG):
            self.disarm('joint maximum guard')
            return

        # QP maximum is 45 deg/s at 30 Hz = 1.5 deg/tick.
        if np.max(np.abs(step)) > (
            self.joint_speed / self.hz + 1e-6
        ):
            self.disarm('joint step guard')
            return

        try:
            self.indy.movetelej_rel(
                jpos=candidate_relative.tolist(),
                vel_ratio=self.vel_ratio,
                acc_ratio=self.acc_ratio,
            )

        except Exception as exc:
            self.get_logger().error(
                f'movetelej_rel failed: {exc}'
            )
            self.disarm('controller error')
            return

        self.relative_joint = candidate_relative

    def shutdown(self):
        self.disarm('node shutdown')


def main(args=None):
    rclpy.init(args=args)

    node = QPTeleop()

    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.shutdown()
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
