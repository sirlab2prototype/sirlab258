#!/usr/bin/env python3

import os
import termios

import rclpy
from rclpy.node import Node
from std_srvs.srv import Trigger


HAND_BOTH = 0xFF
ALL_FINGERS = 0x3F
DIRECTION_FORWARD = 1

NUM_FINGERS = 6


class Mark7Node(Node):
    def __init__(self):
        super().__init__('mark7_node')

        self.declare_parameter('device', '/dev/ttyACM0')
        self.declare_parameter('speed_raw', 50)
        self.declare_parameter('current_raw', 100)

        # Mark7 protocol recommends a slightly negative target
        # for full release because Hall-sensor position counters can drift.
        self.declare_parameter(
            'open_position', [-10, -10, -10, -10, -10, 0],
        )
        self.declare_parameter(
            'grasp_position', [0, 350, 350, 350, 350, 0],
        )

        self.device = str(
            self.get_parameter('device').value
        )
        self.speed_raw = int(
            self.get_parameter('speed_raw').value
        )
        self.current_raw = int(
            self.get_parameter('current_raw').value
        )

        self.fd = self.open_serial(self.device)

        self.create_service(
            Trigger,
            '/gripper/open',
            self.open_callback,
        )
        self.create_service(
            Trigger,
            '/gripper/grasp',
            self.grasp_callback,
        )

        self.get_logger().info(
            f'Mark7 ready: {self.device}, 115200 8-N-1'
        )

    def open_serial(self, device):
        fd = os.open(
            device,
            os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK,
        )

        attrs = termios.tcgetattr(fd)

        # raw-like mode
        attrs[0] = 0
        attrs[1] = 0
        attrs[2] = (
            termios.CS8
            | termios.CLOCAL
            | termios.CREAD
        )
        attrs[2] &= ~termios.PARENB
        attrs[2] &= ~termios.CSTOPB

        if hasattr(termios, 'CRTSCTS'):
            attrs[2] &= ~termios.CRTSCTS

        attrs[3] = 0

        attrs[4] = termios.B115200
        attrs[5] = termios.B115200

        attrs[6][termios.VMIN] = 0
        attrs[6][termios.VTIME] = 0

        termios.tcsetattr(
            fd,
            termios.TCSANOW,
            attrs,
        )
        termios.tcflush(
            fd,
            termios.TCIOFLUSH,
        )

        return fd

    @staticmethod
    def steps_to_raw(steps):
        # Mark7 protocol:
        # raw = (steps + 21) / 2
        #
        # raw=0 means "ignore", so never transmit 0
        # for an actual position command.
        raw = (int(steps) + 21) // 2

        return max(
            1,
            min(255, raw),
        )

    def make_packet(self, positions):
        if len(positions) != NUM_FINGERS:
            raise ValueError(
                f'Expected {NUM_FINGERS} positions, '
                f'got {len(positions)}'
            )

        raw_positions = [
            self.steps_to_raw(value)
            for value in positions
        ]

        # Read these parameters for every command so they can be
        # tuned at runtime with `ros2 param set`.
        speed_raw = int(
            self.get_parameter('speed_raw').value
        )
        current_raw = int(
            self.get_parameter('current_raw').value
        )

        packet = bytes([
            HAND_BOTH,
            ALL_FINGERS,
            speed_raw,
            current_raw,
            *raw_positions,
            DIRECTION_FORWARD,
        ])

        if len(packet) != 11:
            raise RuntimeError(
                f'Invalid packet length: {len(packet)}'
            )

        return packet

    def write_packet(self, packet):
        # Existing verified implementation transmitted
        # every command twice.
        for _ in range(2):
            offset = 0

            while offset < len(packet):
                written = os.write(
                    self.fd,
                    packet[offset:],
                )

                if written <= 0:
                    raise OSError(
                        'Serial write returned no data'
                    )

                offset += written

    def send_preset(self, parameter_name):
        positions = list(
            self.get_parameter(parameter_name).value
        )

        packet = self.make_packet(positions)
        self.write_packet(packet)

        self.get_logger().info(
            f'{parameter_name}: {positions}'
        )

    def open_callback(self, request, response):
        try:
            self.send_preset('open_position')

            response.success = True
            response.message = 'Mark7 open command sent'

        except Exception as exc:
            response.success = False
            response.message = str(exc)

            self.get_logger().error(
                f'Open failed: {exc}'
            )

        return response

    def grasp_callback(self, request, response):
        try:
            self.send_preset('grasp_position')

            response.success = True
            response.message = 'Mark7 grasp command sent'

        except Exception as exc:
            response.success = False
            response.message = str(exc)

            self.get_logger().error(
                f'Grasp failed: {exc}'
            )

        return response

    def destroy_node(self):
        if getattr(self, 'fd', None) is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass

            self.fd = None

        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)

    node = Mark7Node()

    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()

        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
