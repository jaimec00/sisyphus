# vendored: nav2_mppi_controller

This package is **vendored third-party source**, not first-party code of this
repository. It is exempted from the `scripts/check_test_integrity.py`
test-count guard via this marker file (see `discover_packages`): this repo
cannot add tests to upstream code, so demanding results from it would make
`pixi run test` permanently red.

- **Upstream:** `ros-navigation/navigation2`, path `nav2_mppi_controller/`
- **Tag:** `1.3.12` (the version the conda `ros-jazzy-nav2-mppi-controller`
  package also ships)
- **Local patch:** XSIMD **disabled** in `CMakeLists.txt` only
  (`add_definitions(-DXTENSOR_ENABLE_XSIMD)` / `(-DXTENSOR_USE_XSIMD)`
  commented out, `set(XTENSOR_USE_XSIMD 0)`, and `xtensor::use_xsimd` dropped
  from the link line). No `.cpp`/`.hpp` is modified.
- **Why in-tree, not `robot.repos`:** `nav2_mppi_controller` is a subdirectory
  of the navigation2 monorepo (vcstool cannot fetch a single subdir), and it
  needs a local patch; a plain in-tree copy is the only way to carry both.

## Why XSIMD is off (issue #127)

The environment's `xtensor 0.23.9` + `xsimd 14.3.0` are an incompatible pair:
xtensor 0.23.9's `xtensor_simd.hpp` calls the removed old xsimd API
(`xsimd::aligned_allocator<T,A>`, `xsimd::set_simd/load_simd/store_simd`,
`XSIMD_DEFAULT_ALIGNMENT`), none of which exist in xsimd 14.3.0. The conda
`nav2_mppi_controller` 1.3.12 binary was compiled against that pair, so its
MPPI optimizer generates numerically wrong rollouts and the controller drives
the base **exactly opposite** the goal. Because xtensor/xsimd are
header-only, re-pinning the env cannot repair the already-compiled `.so` --
the package must be rebuilt. Building this copy with SIMD off uses xtensor's
scalar path, which is numerically correct (and identical to a correctly
compiled SIMD build), just slower (irrelevant at MPPI's problem size here).

This source-built copy shadows the broken conda binary through the colcon
overlay.

TODO (follow-up, not this PR): restoring SIMD means moving to a compatible
pair (`xtensor 0.25.0` with xsimd 14, as robostack's newer
`ros2-nav2-mppi-controller` build does) and rebuilding with SIMD on.
