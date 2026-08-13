from glob import glob
import os

from setuptools import find_packages, setup

package_name = 'robot_description'

setup(
    name=package_name,
    version='0.0.0',
    packages=find_packages(exclude=['test']),
    # The description ships in `share/<pkg>/`, the ament convention every
    # consumer already knows how to find (`get_package_share_directory`,
    # `$(find robot_description)` in a launch file) -- unlike robot_world's
    # seed, this data is read by *other* packages, not by code in this one.
    #
    # Globbed, never hand-listed: a new .xacro in PR2-PR7 must be installed
    # without anyone remembering to register it here. A file that exists in
    # the source tree but not the install tree is exactly the break the
    # expand/parse gate resolves through the share dir to catch.
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        (os.path.join('share', package_name, 'urdf'), glob('urdf/*')),
        (os.path.join('share', package_name, 'meshes'), glob('meshes/*')),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='Jaime',
    maintainer_email='hejaca00@gmail.com',
    description=(
        'Robot description: URDF/Xacro + MJCF '
        '(4-wheel base + extendable column + 2 arms).'),
    license='MIT',
    extras_require={'test': ['pytest']},
    entry_points={
        'console_scripts': [],
    },
)
