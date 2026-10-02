from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import (
    LaunchConfiguration,
    PathJoinSubstitution,
)
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare


def generate_launch_description():
    mark7_device = LaunchConfiguration('mark7_device')

    mark7_config = PathJoinSubstitution([
        FindPackageShare('capstone_mark7'),
        'config',
        'mark7.yaml',
    ])

    return LaunchDescription([
        DeclareLaunchArgument(
            'mark7_device',
            default_value='/dev/ttyACM0',
            description='Mark7 serial device',
        ),

        Node(
            package='capstone_mark7',
            executable='mark7_node',
            name='mark7_node',
            output='screen',
            parameters=[
                mark7_config,
                {
                    'device': mark7_device,
                },
            ],
        ),
    ])
