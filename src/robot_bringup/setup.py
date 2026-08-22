from glob import glob
import os

from setuptools import find_packages, setup

package_name = 'robot_bringup'

setup(
    name=package_name,
    version='0.0.0',
    packages=find_packages(exclude=['test']),
    # launch/ and params/ are globbed (D24: never a hand-maintained list) so a
    # new launch file or params file is picked up without registering it here.
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        (os.path.join('share', package_name, 'launch'), glob('launch/*')),
        (os.path.join('share', package_name, 'params'), glob('params/*')),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='Jaime',
    maintainer_email='hejaca00@gmail.com',
    description='Bringup: launch files (Python), params (YAML), semantic map.',
    license='MIT',
    extras_require={'test': ['pytest']},
    entry_points={
        'console_scripts': [],
    },
)
