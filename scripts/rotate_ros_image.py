#!/usr/bin/env python3
"""Republish a ROS image rotated 90 degrees counterclockwise."""

import cv2
import rclpy
from cv_bridge import CvBridge
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, qos_profile_sensor_data
from sensor_msgs.msg import CameraInfo, Image


INPUT_ROOT = "/camera_unrotated/cam_wrist/color"
OUTPUT_ROOT = "/camera/cam_wrist/color"


class RotateImageLeft(Node):
    def __init__(self) -> None:
        super().__init__("rotate_wrist_image_left90")
        self._bridge = CvBridge()
        output_qos = QoSProfile(depth=10, reliability=ReliabilityPolicy.RELIABLE)
        self._publisher = self.create_publisher(
            Image,
            f"{OUTPUT_ROOT}/image_raw",
            output_qos,
        )
        self._camera_info_publisher = self.create_publisher(
            CameraInfo,
            f"{OUTPUT_ROOT}/camera_info",
            output_qos,
        )
        self.create_subscription(
            Image,
            f"{INPUT_ROOT}/image_raw",
            self._on_image,
            qos_profile_sensor_data,
        )
        self.create_subscription(
            CameraInfo,
            f"{INPUT_ROOT}/camera_info",
            self._on_camera_info,
            qos_profile_sensor_data,
        )

    def _on_image(self, message: Image) -> None:
        frame = self._bridge.imgmsg_to_cv2(message, desired_encoding="passthrough")
        rotated = cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)
        output = self._bridge.cv2_to_imgmsg(rotated, encoding=message.encoding)
        output.header = message.header
        self._publisher.publish(output)

    def _on_camera_info(self, message: CameraInfo) -> None:
        output = CameraInfo()
        output.header = message.header
        output.height = message.width
        output.width = message.height
        output.distortion_model = message.distortion_model
        output.d = list(message.d)

        # Pixel mapping for a 90-degree counterclockwise rotation:
        # u' = v, v' = width - 1 - u.
        output.k = [
            message.k[4], 0.0, message.k[5],
            0.0, message.k[0], message.width - 1.0 - message.k[2],
            0.0, 0.0, 1.0,
        ]
        output.r = list(message.r)
        output.p = [
            message.p[5], 0.0, message.p[6], message.p[7],
            0.0, message.p[0], message.width - 1.0 - message.p[2], message.p[3],
            0.0, 0.0, 1.0, 0.0,
        ]
        output.binning_x = message.binning_y
        output.binning_y = message.binning_x
        output.roi.x_offset = message.roi.y_offset
        output.roi.y_offset = message.roi.x_offset
        output.roi.height = message.roi.width
        output.roi.width = message.roi.height
        output.roi.do_rectify = message.roi.do_rectify
        self._camera_info_publisher.publish(output)


def main() -> None:
    rclpy.init()
    node = RotateImageLeft()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
