# robot_bringup

Bringup: launch files (Python) + params (YAML) for the assembled robot.

Today this package's one job is to make the robot's TF tree live before any
sim exists:

```sh
ros2 launch robot_bringup robot.launch.py
```

This starts `robot_state_publisher` (against the *installed*
`robot_description`, resolved through the ament index -- no source-tree
fallback, D27/D24) and its standard companion `joint_state_publisher`, so the
full link tree -- including the movable-joint frames (wheels, `column_lift`,
arm joints) -- is published and observable in TF. Launch files and params are
installed via ament and exercised end-to-end by `test/test_tf_tree.py`.

Controllers / `mujoco_ros2_control` (PR8b), Foxglove, MoveIt/Nav2, and the sim
backends are not here yet. See `docs/design/PROJECT.md` for the architecture.
