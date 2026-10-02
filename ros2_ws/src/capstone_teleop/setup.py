from glob import glob
from setuptools import find_packages, setup

package_name = 'capstone_teleop'

setup(
    name=package_name,
    version='0.0.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        ('share/' + package_name + '/launch',
         glob('launch/*.launch.py')),
        
        (
            'share/' + package_name + '/config',
            glob('config/*.yaml'),
        ),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='sirlab2',
    maintainer_email='devcernn1348@gmail.com',
    description='Indy7 Xbox QP teleoperation and capstone launch orchestration.',
    license='TODO: License declaration',
    extras_require={
        'test': [
            'pytest',
        ],
    },
    entry_points={
        'console_scripts': [
            'qp_teleop = capstone_teleop.qp_teleop:main',
            'teleop_gui = capstone_teleop.teleop_gui:main',
        ],
    },
)
