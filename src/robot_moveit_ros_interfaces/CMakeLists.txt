# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

cmake_minimum_required(VERSION 3.10)
project(robot_moveit_ros_interfaces)

find_package(ament_cmake REQUIRED)
find_package(rosidl_default_generators REQUIRED)
# CartesianGoal.srv carries a geometry_msgs/Pose field; rosidl needs the
# package found so its interfaces are in the include map.
find_package(geometry_msgs REQUIRED)

# Only the srv definitions live here -- the same interfaces-package split as
# robot_world_ros_interfaces (D35/R1): the node package (robot_moveit) is
# ament_python, and ament_python cannot generate custom interfaces in this
# environment (its build task is a plain setuptools build that copies the .srv
# and generates no Python module). This is the canonical ROS 2 layout and the
# one place ament_cmake is genuinely required.
rosidl_generate_interfaces(${PROJECT_NAME}
  "srv/CartesianGoal.srv"
  DEPENDENCIES geometry_msgs
)

ament_export_dependencies(rosidl_default_runtime)
ament_export_dependencies(geometry_msgs)

# The gate (`scripts/check_test_integrity.py`) owns first-party packages by
# their git-tracked package.xml and audits `no-result` as a FAILURE, so this
# package must yield >=1 non-linter test: importing the generated interface
# and exercising its response fields. `enable_testing()` is called
# unconditionally rather than inside ament_cmake's `if(BUILD_TESTING)` guard,
# because this workspace sets `-DBUILD_TESTING=OFF` globally in
# colcon_defaults.yaml (a workaround for the vendored mujoco_ros2_control
# stack); under the shared config CTest would be disabled and this package
# would register no test at all, and the gate would audit it `no-result`.
enable_testing()
find_package(ament_cmake_pytest REQUIRED)
ament_add_pytest_test(test_interfaces test/test_interfaces.py)

ament_package()
