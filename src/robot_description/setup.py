import os
from glob import glob

from setuptools import find_packages, setup

package_name = 'robot_description'


def _walk_meshes():
    """Return (install_dir, files) pairs for every file under ``meshes/``.

    Replaces the flat ``glob('meshes/*')`` that PR1–PR3 used: that glob can
    copy top-level files but cannot traverse a nested ``meshes/<subdir>/``
    (``data_files`` cannot copy a directory), and the first mesh set that
    needs a subdir is the arms (D29 deferred the ``os.walk`` form to the first
    mesh-bearing PR, to be written against the real layout rather than a
    guess). The arms' STLs live in ``meshes/arm/`` (10 files shared by both
    the left and right instantiations), so this walks the tree and emits one
    ``(dest_dir, files)`` tuple per subdirectory, installing to
    ``share/robot_description/meshes/<rel>`` -- matching how the URDF
    references them via ``package://robot_description/meshes/arm/<file>.stl``.

    The ``urdf/`` glob stays as-is: it is still a flat set of top-level
    ``*.xacro`` files with no subdirectories, so ``glob('urdf/*')`` remains
    the right tool there (nothing in this file should over-generalize).
    """
    for root, _dirs, files in os.walk('meshes'):
        install_dir = os.path.join('share', package_name, root)
        yield install_dir, sorted(os.path.join(root, f) for f in files)


setup(
    name=package_name,
    version='0.0.0',
    packages=find_packages(exclude=['test']),
    # The description ships in `share/<pkg>/`, the ament convention every
    # consumer already knows how to find (`get_package_share_directory`,
    # `$(find robot_description)` in a launch file) -- unlike robot_world's
    # seed, this data is read by *other* packages, not by code in this one.
    #
    # Globbed/walked, never hand-listed: a new *file* in PR2-PR7 must be
    # installed without anyone remembering to register it here. A file that
    # exists in the source tree but not the install tree is exactly the break
    # the expand/parse gate resolves through the share dir to catch.
    #
    # The meshes half is an `os.walk` since PR4 (the arms): D29 recorded that
    # the flat `glob('meshes/*')` PR1-PR3 used "cannot copy a nested
    # meshes/<subdir>/", and owed the walk to the first PR that actually lands
    # meshes -- the SO-101 arm STLs in meshes/arm/. The `urdf` half stays a
    # flat glob (still no urdf/ subdirs).
    data_files=(
        [('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
         ('share/' + package_name, ['package.xml'])]
        + [(os.path.join('share', package_name, 'urdf'), glob('urdf/*'))]
        + list(_walk_meshes())
    ),
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='Jaime',
    maintainer_email='hejaca00@gmail.com',
    description=(
        'Robot description: URDF/Xacro + MJCF '
        '(3-omniwheel holonomic base + extendable column + 2 arms).'),
    license='MIT',
    extras_require={'test': ['pytest']},
    entry_points={
        'console_scripts': [],
    },
)
