from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    mark7_device = LaunchConfiguration('mark7_device')

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
            parameters=[{
                'device': mark7_device,
            }],
        ),
    ])
