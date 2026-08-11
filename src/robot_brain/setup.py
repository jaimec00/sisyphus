from setuptools import find_packages, setup

package_name = 'robot_brain'

setup(
    name=package_name,
    version='0.0.0',
    packages=find_packages(exclude=['test']),
    # The OpenClaw assets ship *inside* the importable package, not in share/:
    # a file beside the code is readable from a source checkout and from a
    # symlink-installed build alike, with no ament index and no ROS graph.
    package_data={package_name: ['openclaw/*.md', 'openclaw/*.json']},
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
    description='The brain: the OpenClaw agent operating prompt and its config (D21).',
    license='MIT',
    extras_require={'test': ['pytest']},
    entry_points={
        'console_scripts': [],
    },
)
