# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

cmake_minimum_required(VERSION 3.10)
project(robot_nav_interfaces)

find_package(ament_cmake REQUIRED)
find_package(rosidl_default_generators REQUIRED)

# Only the action definition lives here (D30/D36/R1): the skill-level bridge
# (robot_nav) is ament_python, and ament_python cannot generate custom
# interfaces in this environment -- this is the canonical ROS 2
# interfaces-package split, exactly as robot_world_ros_interfaces does it for
# the world services. No geometry_msgs dependency: the goal is a location
# *name* and the result is success/error, so the message carries no pose (the
# bridge owns the pose resolution, never the brain).
rosidl_generate_interfaces(${PROJECT_NAME}
  "action/NavigateToLocation.action"
)

ament_export_dependencies(rosidl_default_runtime)

# The gate (`scripts/check_test_integrity.py`) owns first-party packages by
# their git-tracked package.xml and audits `no-result` as a FAILURE, so this
# package must yield >=1 non-linter test: importing the generated action type
# and exercising its goal/result fields.
#
# `enable_testing()` is called here *unconditionally*, rather than relying on
# ament_cmake's `if(BUILD_TESTING)`-guarded call: this workspace sets
# `-DBUILD_TESTING=OFF` globally (a workaround for the vendored
# mujoco_ros2_control stack), so under the shared config CTest would be
# disabled and this package would register no test at all -- the gate would
# then audit it `no-result`. Enabling the test step for this package only, and
# registering the test outside the guard, produces the JUnit result the gate
# requires without changing the workspace-wide flag. (Mirrors
# robot_world_ros_interfaces.)
enable_testing()
find_package(ament_cmake_pytest REQUIRED)
ament_add_pytest_test(test_interfaces test/test_interfaces.py)

ament_package()
