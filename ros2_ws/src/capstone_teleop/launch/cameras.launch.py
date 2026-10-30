#!/usr/bin/env python3
"""Launch the two serial-bound RGB RealSense cameras and preview windows."""

from pathlib import Path
import re

import yaml

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, LogInfo, OpaqueFunction
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare


_ROS_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_REALSENSE_SERIAL = re.compile(r"^[0-9]+$")


def _camera_nodes(context):
    registry_path = Path(LaunchConfiguration("camera_registry").perform(context))
    camera_config = LaunchConfiguration("camera_config").perform(context)

    with registry_path.open(encoding="utf-8") as stream:
        registry = yaml.safe_load(stream) or {}

    cameras = registry.get("cameras")
    if not isinstance(cameras, list) or not cameras:
        raise ValueError(f"{registry_path}: 'cameras' must be a non-empty list")

    serials = set()
    namespaces = set()
    actions = []
    for index, entry in enumerate(cameras):
        if not isinstance(entry, dict):
            raise ValueError(f"{registry_path}: cameras[{index}] must be a mapping")

        serial = str(entry.get("serial", "")).strip().removeprefix("_")
        namespace = str(entry.get("namespace", "")).strip().strip("/")
        parameters = entry.get("parameters", {})
        if not _REALSENSE_SERIAL.fullmatch(serial):
            raise ValueError(f"{registry_path}: invalid camera serial {serial!r}")
        if not _ROS_NAME.fullmatch(namespace):
            raise ValueError(f"{registry_path}: invalid namespace {namespace!r}")
        if serial in serials or namespace in namespaces:
            raise ValueError(f"{registry_path}: duplicate camera entry")
        if not isinstance(parameters, dict):
            raise ValueError(f"{registry_path}: camera parameters must be a mapping")
        serials.add(serial)
        namespaces.add(namespace)

        topic = f"/{namespace}/camera/color/image_raw"
        actions.extend([
            LogInfo(msg=f"Starting RGB camera serial={serial} topic={topic}"),
            Node(
                package="realsense2_camera",
                executable="realsense2_camera_node",
                namespace=namespace,
                name="camera",
                output="screen",
                parameters=[camera_config, parameters, {"serial_no": serial}],
            ),
            Node(
                package="image_tools",
                executable="showimage",
                name=f"preview_{namespace}",
                output="screen",
                condition=IfCondition(LaunchConfiguration("show_camera")),
                remappings=[("image", topic)],
            ),
        ])
    return actions


def generate_launch_description():
    config_dir = PathJoinSubstitution([
        FindPackageShare("capstone_teleop"), "config",
    ])
    return LaunchDescription([
        DeclareLaunchArgument(
            "camera_registry",
            default_value=PathJoinSubstitution([config_dir, "rgb_cameras.yaml"]),
        ),
        DeclareLaunchArgument(
            "camera_config",
            default_value=PathJoinSubstitution([config_dir, "camera_rgb_only.yaml"]),
        ),
        DeclareLaunchArgument("show_camera", default_value="true"),
        OpaqueFunction(function=_camera_nodes),
    ])
