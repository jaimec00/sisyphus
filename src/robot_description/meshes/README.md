# meshes

Collision and visual geometry for the robot description, installed to
`share/robot_description/meshes/`.

**PR4 is the first mesh-bearing PR.** PR2 authored the 3-omniwheel base from
primitives rather than vendoring LeKiwi's meshes (the upstream omniwheel STL is
15 MB and referenced three times; convex primitives are the better collision
geometry for a mobile base anyway) -- see **D29**. The first real mesh set is the
SO-101 arm STLs in **`arm/`**, cribbed from `Vector-Wangel/XLeRobot` (Apache-2.0,
see `arm/README.md`).

The nested `arm/` subdirectory is exactly why `setup.py` replaced its flat
`glob('meshes/*')` (PR1–PR3) with an `os.walk` form: `data_files` cannot copy a
directory, so a `meshes/<subdir>/x.stl` needs the walk (D27 recorded it, D29
deferred it to the first PR that actually lands meshes -- PR4, written against the
arm's real layout rather than a guess at it). Both the `left` and `right` arm
instantiations reference the same shared `meshes/arm/*.stl` files via
`package://robot_description/meshes/arm/<file>.stl`.

PR5 adds **`meshes/gripper/`** -- the SO-101 parallel-jaw body STLs
(`Fixed_Jaw*`, `Moving_Jaw`) -- using the same `os.walk` install (already in
`setup.py` since PR4) and the same XLeRobot Apache-2.0 source. See
`meshes/gripper/README.md`. The gripper **fingertips** are NOT meshed: they are a
macro-parameterized rigid-primitive link (swap for a compliant fin-ray fingertip
later without re-modeling).

This file is not a placeholder to delete -- it is what makes the top-level
`meshes/` directory exist in the install tree.
