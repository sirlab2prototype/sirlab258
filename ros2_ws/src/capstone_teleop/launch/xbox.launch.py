from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare


def generate_launch_description():
    indy_ip = LaunchConfiguration('indy_ip')

    teleop_config = PathJoinSubstitution([
        FindPackageShare('capstone_teleop'),
        'config',
        'indy7.yaml',
    ])

    return LaunchDescription([
        DeclareLaunchArgument(
            'indy_ip',
            default_value='192.168.1.10',
            description='Indy7 controller IP',
        ),

        Node(
            package='joy',
            executable='joy_node',
            name='joy_node',
            output='screen',
        ),

        Node(
            package='capstone_teleop',
            executable='qp_teleop',
            name='qp_teleop',
            output='screen',
            parameters=[
                teleop_config,
                {'indy_ip': indy_ip},
            ],
        ),
    ])
