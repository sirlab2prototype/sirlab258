from launch import LaunchDescription
from launch.actions import (
    DeclareLaunchArgument,
    IncludeLaunchDescription,
)
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.substitutions import FindPackageShare


def include_launch(name, arguments):
    return IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            PathJoinSubstitution([
                FindPackageShare('capstone_teleop'),
                'launch',
                name,
            ])
        ),
        launch_arguments=arguments.items(),
    )


def generate_launch_description():
    indy_ip = LaunchConfiguration('indy_ip')
    mark7_device = LaunchConfiguration('mark7_device')

    return LaunchDescription([
        DeclareLaunchArgument(
            'indy_ip',
            default_value='192.168.1.10',
            description='Indy7 controller IP',
        ),

        DeclareLaunchArgument(
            'mark7_device',
            default_value='/dev/ttyACM0',
            description='Mark7 serial device',
        ),

        include_launch(
            'indy.launch.py',
            {'indy_ip': indy_ip},
        ),

        include_launch(
            'grip.launch.py',
            {'mark7_device': mark7_device},
        ),

        include_launch(
            'xbox.launch.py',
            {'indy_ip': indy_ip},
        ),
    ])
