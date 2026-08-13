# robot_description

Robot description: URDF/Xacro + MJCF (4-wheel base + extendable column + 2 arms).

Layout — everything here is installed to `share/robot_description/`:

- `urdf/robot.urdf.xacro` — the single entry point. Declares the root frame
  `base_link` and includes the three subassemblies (relative includes, so a
  source checkout and the installed tree expand identically).
- `urdf/{base,column,arm}.xacro` — subassemblies, empty for now; geometry
  lands in PR2 (base), PR3 (column), PR4 (arms) of the URDF roadmap.
- `meshes/` — visual/collision geometry, empty for now.

Expand it by hand with:

```sh
xacro $(ros2 pkg prefix --share robot_description)/urdf/robot.urdf.xacro
```

`test/test_description.py` is the CI gate the later PRs extend. Against the
*installed* share tree, it asserts: the layout is installed; the top level
includes exactly the three subassemblies; `xacro` expands it; `check_urdf`
accepts the expansion; the link set is exactly `EXPECTED_LINKS`; every file the
description names (`<mesh>`, `<texture>`) exists on disk; and the robot is
named. Extend `EXPECTED_LINKS` and `FILE_BEARING_TAGS` as the description
grows — both are module-level constants for that reason. Status: package +
gate; no geometry yet. See
`docs/design/PROJECT.md` for the architecture and `docs/design/decisions.md`
D26/D27 for the hardware lineage and the packaging call.
