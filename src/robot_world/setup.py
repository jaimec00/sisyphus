from setuptools import find_packages, setup

package_name = 'robot_world'

setup(
    name=package_name,
    version='0.0.0',
    packages=find_packages(exclude=['test']),
    # The seed scene ships *inside* the importable package, beside the code
    # that reads it: readable from a source checkout, a symlink-installed
    # build and a wheel alike, with no ament index and no ROS graph. The
    # live-state file is never here -- an install location is not writable.
    package_data={package_name: ['*.json']},
    include_package_data=True,
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='Jaime',
    maintainer_email='hejaca00@gmail.com',
    description='World-state store: the map and the object registry, persisted as JSON (D23).',
    license='MIT',
    extras_require={'test': ['pytest']},
    entry_points={
        'console_scripts': [],
    },
)
