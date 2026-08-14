# robot_description

Robot description: URDF/Xacro + MJCF (3-omniwheel holonomic base + extendable
column + 2 arms).

Layout — everything here is installed to `share/robot_description/`:

- `urdf/robot.urdf.xacro` — the single entry point. Declares the root frame
  `base_link` and includes the three subassemblies (relative includes, so a
  source checkout and the installed tree expand identically).
- `urdf/base.xacro` — the LeKiwi 3-omniwheel holonomic base (D26/D29):
  `base_chassis_link` and `base_footprint` hung off `base_link`, plus one
  `xacro:macro` instantiated three times for the wheels at 60°/180°/300°.
  Every dimension is an `<xacro:property>`; the header names which two are
  sourced from LeRobot's driver and which are estimates.
- `urdf/{column,arm}.xacro` — subassemblies, empty for now; geometry lands in
  PR3 (column) and PR4 (arms) of the URDF roadmap.
- `meshes/` — visual/collision geometry, still empty: the base is primitives
  only, and per D29 the first real mesh set (and the `os.walk` install rewrite
  D27 deferred) arrives with the arms.

Expand it by hand with:

```sh
xacro $(ros2 pkg prefix --share robot_description)/urdf/robot.urdf.xacro
```

`test/test_description.py` is the CI gate the later PRs extend. Against the
*installed* share tree, it asserts: the layout is installed; the top level
includes exactly the three subassemblies; `xacro` expands it; `check_urdf`
accepts the expansion; the link set is exactly `EXPECTED_LINKS`; every file the
description names (`<mesh>`, `<texture>`) exists on disk; the robot is named;
and — added with the base — that there are exactly three `continuous` wheel
joints with the LeRobot motor-key names, each driving its own link; that they
sit on one circle about `base_link` 120° apart with spin axes that come out
radial once composed with their own rpy; that the driver's body→wheel
kinematic matrix, rebuilt from the model, matches LeRobot's own constant (the
one assertion that pins *which* wheel is where — the relational ones all
survive a left/right swap); that `base_footprint` is one wheel radius below
the axle plane; that every link which is a body has visual and collision
geometry and the chassis clears the wheels; that every link which is not a
pure frame has a real inertial; and that `robot_state_publisher` loads the
model (it builds a KDL tree, so it rejects models `check_urdf` accepts). Extend `EXPECTED_LINKS` and `FILE_BEARING_TAGS`
as the description grows — both are module-level constants for that reason.
Status: package + gate + mobile base; column, arms, grippers and MJCF still to
come. See
`docs/design/PROJECT.md` for the architecture and `docs/design/decisions.md`
D26/D27/D29 for the hardware lineage, the packaging call and the base.
