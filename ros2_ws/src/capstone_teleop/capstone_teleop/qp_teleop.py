#!/usr/bin/env python3

import math
import time

import numpy as np
import rclpy

from rclpy.node import Node
from sensor_msgs.msg import Joy, JointState

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

        self.origin_joint = None
        self.relative_joint = np.zeros(6)

        self.cart_filter = CartesianFilter(
            control_hz=self.hz,
            acceleration_mm_s2=300.0,
            alpha=0.35,
        )

        self.indy = IndyDCP3(self.indy_ip)

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

    def joy_callback(self, msg):
        if len(msg.axes) < 8 or len(msg.buttons) < 12:
            return

        self.latest_joy = msg
        self.last_joy_time = time.monotonic()

        share = int(msg.buttons[11])

        if share and not self.last_share:
            if self.armed:
                self.disarm('operator')
            else:
                self.arm()

        self.last_share = share

    def arm(self):
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

        self.origin_joint = self.joint_deg.copy()
        self.relative_joint.fill(0.0)
        self.cart_filter.reset()

        try:
            self.indy.set_teleop_params(
                smooth_factor=0.5,
                cutoff_freq=8.0,
                error_gain=10.0,
            )

            op_state = int(
                self.indy.get_control_data()['op_state']
            )

            # If a previous teleop session is still active,
            # stop it cleanly before starting joint-relative mode.
            if op_state == 17:
                self.indy.stop_teleop()

                deadline = time.monotonic() + 2.0
                while time.monotonic() < deadline:
                    op_state = int(
                        self.indy.get_control_data()['op_state']
                    )
                    if op_state != 17:
                        break
                    time.sleep(0.05)

            # Enter joint-relative teleop and wait until the
            # controller actually reports TELE_OP.
            teleop_ready = False

            for _ in range(5):
                self.indy.start_teleop(
                    method=TELE_JOINT_RELATIVE
                )

                deadline = time.monotonic() + 1.0

                while time.monotonic() < deadline:
                    op_state = int(
                        self.indy.get_control_data()['op_state']
                    )

                    if op_state == 17:
                        teleop_ready = True
                        break

                    time.sleep(0.05)

                if teleop_ready:
                    break

            if not teleop_ready:
                raise RuntimeError(
                    f'Failed to enter TELE_OP '
                    f'(last op_state={op_state})'
                )

        except Exception as exc:
            self.get_logger().error(
                f'Could not start teleop: {exc}'
            )
            return

        # Capture a fresh origin only after TELE_OP is ready.
        self.origin_joint = self.joint_deg.copy()
        self.relative_joint.fill(0.0)
        self.cart_filter.reset()

        self.armed = True

        self.get_logger().warning(
            'QP TELEOP ARMED'
        )

    def disarm(self, reason):
        if not self.armed:
            return

        self.armed = False
        self.cart_filter.reset()

        try:
            self.indy.stop_teleop()
        except Exception as exc:
            self.get_logger().error(
                f'stop_teleop failed: {exc}'
            )

        self.get_logger().warning(
            f'QP TELEOP DISARMED: {reason}'
        )

    def control_loop(self):
        if not self.armed:
            return

        now = time.monotonic()

        if (
            self.latest_joy is None
            or self.last_joy_time is None
            or now - self.last_joy_time > 0.25
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
