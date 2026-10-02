from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.substitutions import FindPackageShare


def generate_launch_description():
    indy_ip = LaunchConfiguration('indy_ip')

    indy_driver = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            PathJoinSubstitution([
                FindPackageShare('indy_driver'),
                'launch',
                'indy_bringup.launch.py',
            ])
        ),
        launch_arguments={
            'indy_type': 'indy7',
            'indy_ip': indy_ip,
            'launch_rviz': 'false',
        }.items(),
    )

    return LaunchDescription([
        DeclareLaunchArgument(
            'indy_ip',
            default_value='192.168.1.10',
            description='Indy7 controller IP',
        ),
        indy_driver,
    ])
