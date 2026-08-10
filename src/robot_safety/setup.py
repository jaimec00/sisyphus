from setuptools import find_packages, setup

package_name = 'robot_safety'

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
    description='Safety layer: joint/velocity/force limits, collision gate, e-stop.',
    license='MIT',
    extras_require={'test': ['pytest']},
    entry_points={
        'console_scripts': [],
    },
)
