#!/usr/bin/env python3

import math

import rclpy
from rclpy.node import Node

from geometry_msgs.msg import TwistStamped
from sensor_msgs.msg import Joy


class XboxMapper(Node):
    def __init__(self):
        super().__init__('xbox_mapper')

        self.declare_parameter('linear_speed', 0.5)
        self.declare_parameter('angular_speed', 0.5)

        self.declare_parameter('deadzone', 0.18)
        self.declare_parameter('response_exponent', 2.0)
        self.declare_parameter('lowpass_alpha', 0.35)

        self.declare_parameter('linear_accel_limit', 0.3)
        self.declare_parameter('control_hz', 30.0)
        self.declare_parameter('joy_timeout', 0.25)

        self.declare_parameter('frame_id', 'link0')

        self.linear_speed = float(
            self.get_parameter('linear_speed').value
        )
        self.angular_speed = float(
            self.get_parameter('angular_speed').value
        )

        self.deadzone = float(
            self.get_parameter('deadzone').value
        )
        self.response_exponent = float(
            self.get_parameter('response_exponent').value
        )
        self.lowpass_alpha = float(
            self.get_parameter('lowpass_alpha').value
        )

        self.linear_accel_limit = float(
            self.get_parameter('linear_accel_limit').value
        )
        self.control_hz = float(
            self.get_parameter('control_hz').value
        )
        self.joy_timeout = float(
            self.get_parameter('joy_timeout').value
        )

        self.frame_id = str(
            self.get_parameter('frame_id').value
        )

        self.latest_joy = None
        self.last_joy_time = None

        # Low-pass state
        self.filtered_linear = [0.0, 0.0, 0.0]

        # Final command after acceleration limiting
        self.linear_command = [0.0, 0.0, 0.0]

        self.publisher = self.create_publisher(
            TwistStamped,
            '/servo_node/delta_twist_cmds',
            10,
        )

        self.subscription = self.create_subscription(
            Joy,
            '/joy',
            self.joy_callback,
            10,
        )

        self.timer = self.create_timer(
            1.0 / self.control_hz,
            self.control_loop,
        )

        self.get_logger().info(
            'Xbox mapper ready | '
            f'{self.control_hz:.0f} Hz | '
            f'linear={self.linear_speed:.2f} m/s | '
            f'deadzone={self.deadzone:.2f} | '
            f'exp={self.response_exponent:.1f} | '
            f'alpha={self.lowpass_alpha:.2f} | '
            f'accel={self.linear_accel_limit:.2f} m/s^2 | '
            f'frame={self.frame_id}'
        )

    def joy_callback(self, msg):
        if len(msg.axes) < 8 or len(msg.buttons) < 12:
            return

        self.latest_joy = msg
        self.last_joy_time = self.get_clock().now()

    def shape_axis(self, value):
        """Deadzone -> renormalize -> nonlinear response curve."""
        magnitude = abs(value)

        if magnitude <= self.deadzone:
            return 0.0

        normalized = (
            magnitude - self.deadzone
        ) / (
            1.0 - self.deadzone
        )

        shaped = normalized ** self.response_exponent

        return math.copysign(shaped, value)

    def shape_trigger(self, raw):
        # Xbox trigger:
        # released = +1
        # pressed  = -1
        amount = (1.0 - raw) / 2.0
        amount = max(0.0, min(1.0, amount))

        if amount <= self.deadzone:
            return 0.0

        normalized = (
            amount - self.deadzone
        ) / (
            1.0 - self.deadzone
        )

        return normalized ** self.response_exponent

    def lowpass(self, previous, target):
        a = self.lowpass_alpha
        return a * target + (1.0 - a) * previous

    def rate_limit(self, current, target, dt):
        max_delta = self.linear_accel_limit * dt
        delta = target - current

        if delta > max_delta:
            delta = max_delta
        elif delta < -max_delta:
            delta = -max_delta

        return current + delta

    def publish_zero(self, now):
        # Input loss is a safety condition:
        # do not preserve filter/ramp state.
        self.filtered_linear = [0.0, 0.0, 0.0]
        self.linear_command = [0.0, 0.0, 0.0]

        msg = TwistStamped()
        msg.header.stamp = now.to_msg()
        msg.header.frame_id = self.frame_id
        self.publisher.publish(msg)

    def control_loop(self):
        now = self.get_clock().now()

        if self.latest_joy is None or self.last_joy_time is None:
            self.publish_zero(now)
            return

        age = (
            now - self.last_joy_time
        ).nanoseconds / 1e9

        if age > self.joy_timeout:
            self.publish_zero(now)
            return

        joy = self.latest_joy

        left_x = self.shape_axis(joy.axes[0])
        left_y = self.shape_axis(joy.axes[1])

        lt = self.shape_trigger(joy.axes[2])

        right_x = self.shape_axis(joy.axes[3])
        right_y = self.shape_axis(joy.axes[4])

        rt = self.shape_trigger(joy.axes[5])

        lb = joy.buttons[4]
        rb = joy.buttons[5]

        target_linear = [
            (rt - lt) * self.linear_speed,
            right_x * self.linear_speed,
            right_y * self.linear_speed,
        ]

        # Smooth target velocity.
        for i in range(3):
            self.filtered_linear[i] = self.lowpass(
                self.filtered_linear[i],
                target_linear[i],
            )

        # Then limit both acceleration and deceleration.
        dt = 1.0 / self.control_hz

        for i in range(3):
            self.linear_command[i] = self.rate_limit(
                self.linear_command[i],
                self.filtered_linear[i],
                dt,
            )

        twist = TwistStamped()
        twist.header.stamp = now.to_msg()
        twist.header.frame_id = self.frame_id

        twist.twist.linear.x = self.linear_command[0]
        twist.twist.linear.y = self.linear_command[1]
        twist.twist.linear.z = self.linear_command[2]

        # Rotation gets deadzone + nonlinear response.
        # We are not adding angular acceleration limiting yet.
        twist.twist.angular.x = left_x * self.angular_speed
        twist.twist.angular.y = left_y * self.angular_speed
        twist.twist.angular.z = (
            float(rb - lb) * self.angular_speed
        )

        self.publisher.publish(twist)


def main(args=None):
    rclpy.init(args=args)

    node = XboxMapper()

    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
