from setuptools import find_packages, setup

package_name = 'robot_world_ros'

setup(
    name=package_name,
    version='0.0.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='Jaime',
    maintainer_email='hejaca00@gmail.com',
    description=(
        'ROS 2 world-state query service: a thin adapter over the robot_world'
        ' store (D35).'),
    license='MIT',
    extras_require={'test': ['pytest']},
    entry_points={
        'console_scripts': [
            'world_query_node = robot_world_ros.world_query_node:main',
        ],
    },
)
