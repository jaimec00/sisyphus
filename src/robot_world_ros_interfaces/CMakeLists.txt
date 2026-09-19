# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

cmake_minimum_required(VERSION 3.10)
project(robot_world_ros_interfaces)

find_package(ament_cmake REQUIRED)
find_package(rosidl_default_generators REQUIRED)

# Only the srv definitions live here (D35/R1): the node package
# (robot_world_ros) is ament_python, and ament_python cannot generate custom
# interfaces in this environment (its build task is a plain setuptools build
# that copies the .srv and generates no Python module). This is the canonical
# ROS 2 interfaces-package split, and the one place ament_cmake is genuinely
# required.
rosidl_generate_interfaces(${PROJECT_NAME}
  "srv/GetWorld.srv"
)

ament_export_dependencies(rosidl_default_runtime)

# The gate (`scripts/check_test_integrity.py`) owns first-party packages by
# their git-tracked package.xml and audits `no-result` as a FAILURE, so this
# package must yield >=1 non-linter test: importing the generated interface
# and exercising its response field.
#
# `enable_testing()` is called here *unconditionally*, rather than relying on
# ament_cmake's `if(BUILD_TESTING)`-guarded call: this workspace sets
# `-DBUILD_TESTING=OFF` globally in colcon_defaults.yaml (a workaround for the
# vendored mujoco_ros2_control stack, whose upstream test binaries do not link
# against the conda glfw), so under the shared config CTest would be disabled
# and this package would register no test at all -- the gate would then audit
# it `no-result`. Enabling the test step for this package only, and registering
# the test outside the guard, produces the JUnit result the gate requires
# without changing the workspace-wide flag.
enable_testing()
find_package(ament_cmake_pytest REQUIRED)
# The smoke test is the importable generated interface: it must generate, be
# importable from Python, and carry the one response field the node fills in.
# `test_interfaces.py` also writes the JUnit XML the gate reads.
ament_add_pytest_test(test_interfaces test/test_interfaces.py)

ament_package()
