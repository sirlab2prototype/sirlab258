#!/usr/bin/env python3

import math
import time

import numpy as np
import rclpy

from rclpy.node import Node
from sensor_msgs.msg import Joy, JointState
from indy_interfaces.srv import IndyService
from std_srvs.srv import Trigger
from rclpy.qos import QoSProfile, ReliabilityPolicy, DurabilityPolicy
from std_msgs.msg import Float32, Float64, String

from neuromeka import IndyDCP3

from .qp_core import (
    JOINT_MAX_DEG,
    JOINT_MIN_DEG,
    qp_step,
    shape_axis,
    shape_trigger,
)
from .input_profile import MotionProfile


MSG_RECOVER = 1
MSG_MOVE_ZERO = 3
MSG_TELE_JOINT_RLT = 7
MSG_TELE_STOP = 8


class QPTeleop(Node):
    def __init__(self):
        super().__init__('qp_teleop')

        self.declare_parameter('indy_ip', '192.168.1.114')
        self.declare_parameter('control_hz', 30.0)
        self.declare_parameter('linear_step_mm', 5.0)
        self.declare_parameter('linear_step_delta_mm', 0.25)
        self.declare_parameter('min_linear_step_mm', 0.25)
        self.declare_parameter('max_linear_step_mm', 5.0)
        self.declare_parameter('angular_speed_rad_s', 0.3)
        self.declare_parameter('joint_speed_deg_s', 45.0)
        self.declare_parameter('soft_margin_deg', 15.0)
        self.declare_parameter('vel_ratio', 0.8)
        self.declare_parameter('acc_ratio', 1.0)

        # QP tuning
        self.declare_parameter('orientation_weight', 25.0)
        self.declare_parameter('centering_weight', 0.08)
        self.declare_parameter('damping', 0.04)

        # Xbox input shaping
        self.declare_parameter('stick_deadzone', 0.18)
        self.declare_parameter('stick_exponent', 2.0)
        self.declare_parameter('trigger_deadzone', 0.08)
        self.declare_parameter('trigger_exponent', 2.0)
        self.declare_parameter('cartesian_accel_mm_s2', 600.0)
        self.declare_parameter('yaw_accel_rad_s2', 1.5)

        # Indy TELE_OP controller tuning
        self.declare_parameter('teleop_smooth_factor', 0.5)
        self.declare_parameter('teleop_cutoff_freq', 8.0)
        self.declare_parameter('teleop_error_gain', 10.0)

        self.indy_ip = str(self.get_parameter('indy_ip').value)
        self.hz = float(self.get_parameter('control_hz').value)
        self.linear_step = float(
            self.get_parameter('linear_step_mm').value
        )
        self.linear_step_delta = float(
            self.get_parameter('linear_step_delta_mm').value
        )
        self.min_linear_step = float(
            self.get_parameter('min_linear_step_mm').value
        )
        self.max_linear_step = float(
            self.get_parameter('max_linear_step_mm').value
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

        self.orientation_weight = float(
            self.get_parameter('orientation_weight').value
        )
        self.centering_weight = float(
            self.get_parameter('centering_weight').value
        )
        self.damping = float(
            self.get_parameter('damping').value
        )

        self.stick_deadzone = float(
            self.get_parameter('stick_deadzone').value
        )
        self.stick_exponent = float(
            self.get_parameter('stick_exponent').value
        )
        self.trigger_deadzone = float(
            self.get_parameter('trigger_deadzone').value
        )
        self.trigger_exponent = float(
            self.get_parameter('trigger_exponent').value
        )

        self.teleop_smooth_factor = float(
            self.get_parameter('teleop_smooth_factor').value
        )
        self.teleop_cutoff_freq = float(
            self.get_parameter('teleop_cutoff_freq').value
        )
        self.teleop_error_gain = float(
            self.get_parameter('teleop_error_gain').value
        )

        self._validate_parameters()
        self.motion_profile = MotionProfile(
            self.hz, self.max_linear_step, self.angular_speed,
            self.stick_deadzone, self.stick_exponent,
            self.trigger_deadzone, self.trigger_exponent,
            float(self.get_parameter('cartesian_accel_mm_s2').value),
            float(self.get_parameter('yaw_accel_rad_s2').value),
        )

        self.latest_joy = None
        self.last_joy_time = None

        self.joint_deg = None
        self.last_joint_time = None

        self.armed = False
        self.last_share = 0
        self.last_a = 0
        self.last_b = 0
        self.last_lb = 0
        self.last_rb = 0

        self.origin_joint = None
        self.relative_joint = np.zeros(6)
        self.gripper_state = 0.0

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

        self.speed_pub = self.create_publisher(
            Float64,
            '/capstone/teleop_speed_mm',
            10,
        )

        self.status_pub = self.create_publisher(
            String,
            '/capstone/teleop_status',
            10,
        )

        # Publish detached action/state copies for ACT data collection.
        self.action_pub = self.create_publisher(
            JointState,
            '/capstone/teleop_action',
            10,
        )
        self.gripper_state_pub = self.create_publisher(
            Float32,
            '/capstone/gripper_state',
            QoSProfile(
                depth=1,
                reliability=ReliabilityPolicy.RELIABLE,
                durability=DurabilityPolicy.TRANSIENT_LOCAL,
            ),
        )

        self.create_timer(
            0.5,
            self.publish_speed,
        )

        self.create_timer(
            0.2,
            self.publish_status,
        )
        self.mode_future = None
        self.arming = False
        self.waiting_for_fresh_joint = False
        self.arm_token = 0

        # Auto-arm is allowed only at startup and after CAPSTONE HOME.
        # Actual ARM waits for fresh robot state and neutral Xbox input.
        self.auto_arm_pending = True
        self.auto_arm_reason = 'startup'
        self.home_rearm_pending = False

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

        self.auto_arm_timer = self.create_timer(
            0.1,
            self.check_auto_arm,
        )

        self.get_logger().info(
            'QP teleop ready | '
            'Left stick=X/Y | '
            'RT/LT=Z | '
            'Right stick X=Rz | '
            'D-pad X=Rx, D-pad Y=Ry | '
            'Share=ARM/DISARM'
        )

    def _validate_parameters(self):
        if self.hz <= 0.0:
            raise ValueError('control_hz must be > 0')

        if self.linear_step_delta <= 0.0:
            raise ValueError(
                'linear_step_delta_mm must be > 0'
            )

        if self.min_linear_step <= 0.0:
            raise ValueError(
                'min_linear_step_mm must be > 0'
            )

        if self.max_linear_step < self.min_linear_step:
            raise ValueError(
                'max_linear_step_mm must be >= '
                'min_linear_step_mm'
            )

        if not (
            self.min_linear_step
            <= self.linear_step
            <= self.max_linear_step
        ):
            raise ValueError(
                'linear_step_mm must be inside '
                '[min_linear_step_mm, max_linear_step_mm]'
            )

        if self.angular_speed < 0.0:
            raise ValueError(
                'angular_speed_rad_s must be >= 0'
            )

        if self.joint_speed <= 0.0:
            raise ValueError(
                'joint_speed_deg_s must be > 0'
            )

        if self.soft_margin < 0.0:
            raise ValueError(
                'soft_margin_deg must be >= 0'
            )

        if not 0.0 <= self.stick_deadzone < 1.0:
            raise ValueError(
                'stick_deadzone must be in [0, 1)'
            )

        if not 0.0 <= self.trigger_deadzone < 1.0:
            raise ValueError(
                'trigger_deadzone must be in [0, 1)'
            )

        if self.stick_exponent <= 0.0:
            raise ValueError(
                'stick_exponent must be > 0'
            )

        if self.trigger_exponent <= 0.0:
            raise ValueError(
                'trigger_exponent must be > 0'
            )

    def _shape_axis(self, value):
        return shape_axis(
            value,
            deadzone=self.stick_deadzone,
            exponent=self.stick_exponent,
        )

    def _shape_trigger(self, value):
        return shape_trigger(
            value,
            deadzone=self.trigger_deadzone,
            exponent=self.trigger_exponent,
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
            self.motion_profile.reset()
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
        lb = int(msg.buttons[4])
        rb = int(msg.buttons[5])

        # View is only a modifier for combo commands.
        # View by itself does not change ARM state.
        if view and not getattr(self, 'last_view', 0):
            self.view_combo_used = False

        # View + LB/RB adjusts Cartesian step.
        # View itself does not change ARM state.
        if view:
            if lb and not self.last_lb:
                self.adjust_speed(-1)

            if rb and not self.last_rb:
                self.adjust_speed(1)

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
        self.last_lb = lb
        self.last_rb = rb

    def publish_status(self):
        msg = String()

        if self.armed:
            msg.data = 'ARMED'
        elif self.arming:
            msg.data = 'ARMING'
        else:
            msg.data = 'DISARMED'

        self.status_pub.publish(msg)

    def publish_speed(self):
        msg = Float64()
        msg.data = float(self.linear_step)
        self.speed_pub.publish(msg)

    def adjust_speed(self, direction):
        value = (
            self.linear_step
            + direction * self.linear_step_delta
        )

        value = max(
            self.min_linear_step,
            min(self.max_linear_step, value),
        )

        # Avoid floating-point values such as 3.249999999.
        self.linear_step = round(value, 6)

        speed_mm_s = self.linear_step * self.hz
        scale = self.linear_step / self.max_linear_step

        self.get_logger().warning(
            f'TELEOP SPEED: {self.linear_step:.2f} mm/tick '
            f'(~{speed_mm_s:.1f} mm/s, '
            f'{scale:.0%})'
        )

        self.publish_speed()

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
                self.gripper_state = (
                    1.0 if label.lower() == 'grasp' else 0.0
                )
                gripper_msg = Float32()
                gripper_msg.data = self.gripper_state
                self.gripper_state_pub.publish(gripper_msg)
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
        # HOME must run outside TELE_OP.
        # Rearming is scheduled again only after HOME command succeeds.
        self.auto_arm_pending = False
        self.home_rearm_pending = False

        if self.armed or self.arming:
            # HOME sends its own TELE_STOP request below.
            # Update local state only, avoiding a duplicate stop request.
            self._set_disarmed_state('capstone home')

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

            self.home_rearm_pending = True
            self.auto_arm_reason = 'CAPSTONE HOME complete'
            self.auto_arm_pending = True

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

        # Recover/Zero must not race with active QP TELE_OP.
        self.auto_arm_pending = False
        self.home_rearm_pending = False

        if self.armed or self.arming:
            self._set_disarmed_state(
                f'{label.lower()} command'
            )

        stop_request = IndyService.Request()
        stop_request.data = MSG_TELE_STOP

        self.get_logger().warning(
            f'Preparing {label}: stopping TELE_OP first...'
        )

        self.command_future = (
            self.indy_service_client.call_async(stop_request)
        )

        self.command_future.add_done_callback(
            lambda future, command_data=data, command=label:
                self.robot_command_after_stop(
                    future,
                    command_data,
                    command,
                )
        )

    def robot_command_after_stop(self, future, data, label):
        try:
            response = future.result()

            if not response.success:
                self.get_logger().error(
                    f'Could not stop TELE_OP before {label}'
                )
                return

            motion = self.indy.get_motion_data()

            if motion.get('is_in_motion', False):
                self.get_logger().error(
                    f'Cannot run {label}: robot still moving'
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
                lambda done_future, command=label:
                    self.robot_command_done(
                        done_future,
                        command,
                    )
            )

        except Exception as exc:
            self.get_logger().error(
                f'Could not prepare {label}: {exc}'
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

    def xbox_is_neutral(self):
        joy = self.latest_joy
        return (
            joy is not None
            and self.motion_profile.neutral(joy.axes, joy.buttons)
        )

    def check_auto_arm(self):
        if not self.auto_arm_pending:
            return

        if self.armed or self.arming:
            return

        now = time.monotonic()

        # Require a live controller.
        if (
            self.latest_joy is None
            or self.last_joy_time is None
            or now - self.last_joy_time > 0.5
        ):
            return

        # Require a fresh robot state.
        if (
            self.joint_deg is None
            or self.last_joint_time is None
            or now - self.last_joint_time > 0.5
        ):
            return

        if not self.indy_service_client.service_is_ready():
            return

        # Never automatically ARM while the operator is touching
        # a motion control or button.
        if not self.xbox_is_neutral():
            return

        if self.home_rearm_pending:
            try:
                motion = self.indy.get_motion_data()
            except Exception as exc:
                self.get_logger().error(
                    f'Cannot verify HOME completion: {exc}'
                )
                return

            if motion.get('is_in_motion', False):
                return

            target = np.asarray(
                self.capstone_home_joint_deg,
                dtype=float,
            )

            error_deg = np.max(
                np.abs(self.joint_deg - target)
            )

            # Do not re-enter TELE_OP until the robot has actually
            # arrived at the requested HOME pose.
            if error_deg > 0.5:
                return

            self.home_rearm_pending = False

        reason = self.auto_arm_reason

        # Consume the automatic request before entering ARM.
        # If ARM itself fails, the operator can still use Share.
        self.auto_arm_pending = False

        self.get_logger().warning(
            f'AUTO ARM: {reason}'
        )

        self.arm()

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
                smooth_factor=self.teleop_smooth_factor,
                cutoff_freq=self.teleop_cutoff_freq,
                error_gain=self.teleop_error_gain,
            )
        except Exception as exc:
            self.get_logger().error(
                f'Could not set teleop parameters: {exc}'
            )
            return

        self.arming = True
        self.arm_token += 1
        token = self.arm_token

        self.relative_joint.fill(0.0)
        self.motion_profile.reset()

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

    def _set_disarmed_state(self, reason):
        # Invalidate any pending ARM callback immediately.
        self.arm_token += 1
        self.arming = False
        self.waiting_for_fresh_joint = False
        self.armed = False
        self.relative_joint.fill(0.0)
        self.motion_profile.reset()

        self.get_logger().warning(
            f'QP TELEOP DISARMED: {reason}'
        )

    def disarm(self, reason):
        if not self.armed and not self.arming:
            return

        self._set_disarmed_state(reason)

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

        # Final operator mapping:
        #
        # Left stick:
        #   forward/back -> Cartesian X
        #   left/right   -> Cartesian Y
        #
        # RT/LT:
        #   up/down      -> Cartesian Z
        #
        # Right stick:
        #   left/right   -> Rz (yaw)
        #
        # D-pad:
        #   left/right   -> Rx
        #   up/down      -> Ry
        #
        # LB/RB:
        #   reserved for View + speed adjustment

        try:
            translation, rotation = self.motion_profile.update(
                joy.axes, self.linear_step
            )
        except ValueError as exc:
            self.get_logger().error(
                f"Invalid Xbox input: {exc}"
            )
            self.disarm("invalid Xbox input")
            return

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
                orientation_weight=self.orientation_weight,
                centering_weight=self.centering_weight,
                damping=self.damping,
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

        # Enforce the configured maximum joint step.
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

        action_msg = JointState()
        action_msg.header.stamp = self.get_clock().now().to_msg()
        action_msg.name = [
            'joint0', 'joint1', 'joint2', 'joint3', 'joint4', 'joint5',
        ]
        action_msg.position = np.radians(candidate_joint).tolist()
        self.action_pub.publish(action_msg)

    def shutdown(self):
        self.auto_arm_pending = False
        self.home_rearm_pending = False

        if self.armed or self.arming:
            self._set_disarmed_state('node shutdown')
        else:
            # Invalidate an ARM callback that may still be pending.
            self.arm_token += 1
            self.arming = False
            self.waiting_for_fresh_joint = False
            self.armed = False
            self.relative_joint.fill(0.0)
            self.motion_profile.reset()

        # Final synchronous stop guard.
        # Do not rely on an async ROS service while destroying the node.
        try:
            self.indy.stop_teleop()
            self.get_logger().info(
                'TELE_OP stop confirmed for node shutdown'
            )
        except Exception as exc:
            self.get_logger().error(
                f'Final stop_teleop failed: {exc}'
            )


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
