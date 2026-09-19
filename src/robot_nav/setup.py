from glob import glob
import os

from setuptools import find_packages, setup

package_name = 'robot_nav'

setup(
    name=package_name,
    version='0.0.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        # Launch files and params are shipped as-is: the bringup includes
        # nav.launch.py through the ament index, and the launch file reads its
        # params from this package's share directory, so both must be installed
        # (the pattern robot_bringup / robot_world_ros already use).
        (os.path.join('share', package_name, 'launch'),
            glob('launch/*.launch.py')),
        (os.path.join('share', package_name, 'params'),
            glob('params/*.yaml')),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='Jaime',
    maintainer_email='hejaca00@gmail.com',
    description=(
        'Nav2 localization layer: a static map derived from the world store and'
        ' a ground-truth odom -> base_link transform (D36).'),
    license='MIT',
    extras_require={'test': ['pytest']},
    entry_points={
        'console_scripts': [
            'map_node = robot_nav.map_node:main',
            'ground_truth_odom = robot_nav.ground_truth_odom:main',
            'omni_base_controller = robot_nav.omni_base_controller:main',
        ],
    },
)
