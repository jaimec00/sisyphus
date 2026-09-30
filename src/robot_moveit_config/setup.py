from glob import glob
import os

from setuptools import find_packages, setup

package_name = 'robot_moveit_config'


def _walk_meshes():
    """Return (install_dir, files) pairs for every file under ``meshes/``."""
    for root, _dirs, files in os.walk('meshes'):
        install_dir = os.path.join('share', package_name, root)
        yield install_dir, sorted(os.path.join(root, f) for f in files)


setup(
    name=package_name,
    version='0.0.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        # Globbed, never hand-listed: a new config file must install without
        # anyone remembering to register it here (robot_description's rule).
        (os.path.join('share', package_name, 'config'), glob('config/*')),
        (os.path.join('share', package_name, 'launch'), glob('launch/*')),
        # The .setup_assistant pointer file moveit_configs_utils reads to find
        # the URDF (in robot_description) and the SRDF (here).
        (os.path.join('share', package_name), ['.setup_assistant']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='Jaime',
    maintainer_email='hejaca00@gmail.com',
    description=(
        'MoveIt 2 configuration for Sisyphus: SRDF, kinematics, joint limits,'
        ' OMPL pipeline, and a headless move_group launch.'),
    license='MIT',
    extras_require={'test': ['pytest']},
    entry_points={
        'console_scripts': [],
    },
)
