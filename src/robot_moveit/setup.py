from glob import glob
import os

from setuptools import find_packages, setup

package_name = 'robot_moveit'

setup(
    name=package_name,
    version='0.0.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        (os.path.join('share', package_name, 'launch'), glob('launch/*')),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='Jaime',
    maintainer_email='hejaca00@gmail.com',
    description=(
        'MoveIt runtime glue: a planning-scene bridge that feeds world objects'
        ' from robot_world into the MoveIt planning scene.'),
    license='MIT',
    extras_require={'test': ['pytest']},
    entry_points={
        'console_scripts': [
            'planning_scene_bridge = robot_moveit.planning_scene_bridge:main',
        ],
    },
)
