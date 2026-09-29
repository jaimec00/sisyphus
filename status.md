# status.md — feature i139-mujoco-312-alignment (issue #139)

Manager rulings recorded before dispatch. Workers: implementer -> red-team -> test-runner
(all deepseek-v4-flash). Worktree `~/worktrees/i139-mujoco-312-alignment`, branch
`feat/i139-mujoco-312-alignment`, cut from `origin/main` @ `6a4d51f`.

## Brief (issue #139)
Align the ROS sim (dfki-ric `mujoco_ros2_control`) onto MuJoCo **3.12.0**, removing the
3.9/3.12 version split. The ROS path FetchContent-source-builds its own MuJoCo 3.9.0
(`GIT_TAG 3.9.0` hardcoded in the vendored CMakeLists); the Python path uses conda 3.12.0.
The probe (already done) confirmed bumping `GIT_TAG 3.9.0 -> 3.12.0` compiles clean (no API
drift; the `mjv_moveCamera` "drift" was a header-shadow artifact). The probe only verified
COMPILATION, not runtime — a fresh worktree must reproduce the clean 3.12 build before PR.

## Rulings (binding)

### R1 — Mechanism: owned FetchContent override via `FETCHCONTENT_SOURCE_DIR_MUJOCO` (NOT a fork, NOT find_package)
Override dfki-ric's `GIT_TAG 3.9.0` from **our tracked** files by pointing CMake's
FetchContent at a locally-pinned MuJoCo 3.12.0 source tree:

- **`robot.repos`**: add a `mujoco` repo pinned at commit
  `13827e9ee56f097f57acf69ae52b078f9839682d` (the `3.12.0` tag). `vcs import` drops it at
  `src/mujoco/`.
- **`.gitignore`**: add `src/mujoco/`.
- **`pixi.toml` `[tasks] build`**: append `-DFETCHCONTENT_SOURCE_DIR_MUJOCO=${PWD}/src/mujoco`
  to the existing `colcon build --cmake-args ...` (pixi runs tasks from the project root, so
  `${PWD}` is the worktree root; the flag is a harmless unused cache var for the non-mujoco
  packages).

**Why not `find_package` (conda 3.12 CMake package):** the conda mujoco config exports only
`mujoco::mujoco` + `mujoco::libmujoco_simulate` — NOT `mujoco::platform_ui_adapter` or
`lodepng`, which the vendored CMakeLists hard-links. `find_package` cannot satisfy the link
surface without editing vendored source. Ruled out.

**Why not fork:** the override is standard, keeps us on upstream dfki-ric, and needs no fork
to maintain. Fork is the FALLBACK only — if the implementer finds the override genuinely
unwieldy (build does not produce a clean 3.12 link after a real attempt), STOP and escalate
to the manager (do NOT silently switch to fork).

**The `_deps/mujoco-src/{include,simulate}` include dirs in the vendored CMakeLists become
nonexistent under the override** (the source is at `src/mujoco`, not `_deps/mujoco-src`).
This is benign: the real headers resolve via target interfaces (`mujoco::mujoco` ->
`src/mujoco/include`; `mujoco::platform_ui_adapter` -> `src/mujoco/simulate`), and
nonexistent `include_directories` entries are no-ops. The implementer MUST verify this
empirically (a clean build + `objdump -p` showing `NEEDED libmujoco.so.3.12.0`), not reason it.

### R2 — The header-shadow patch is obsolete and must not reappear
The patch was a git-untracked edit to the vendored CMakeLists (not tracked, not committed).
A fresh `vcs import` does not carry it. Do NOT re-apply it, and do NOT add any tracked file
that reproduces it. Verify the clean 3.12 build succeeds WITHOUT it (the override makes it
unnecessary: both header sets are 3.12, so include order is irrelevant).

### R3 — Version-split regression test (required)
The existing `test_base_delivers_commanded_vx_through_the_ros_chain` passes on BOTH 3.9 and
3.12, so it cannot catch a re-split. Add a lightweight, honest test that fails if the ROS sim
re-splits to 3.9. Recommended shape (implementer refines the mechanism, but keep it a real
assertion, not a tautology):
- Live in `src/robot_bringup/test/` (the ROS-sim owner). Skip if
  `_have_package('mujoco_ros2_control')` is false (same guard as the existing bringup tests).
- Assert the installed plugin links 3.12: locate the installed prefix (ament index /
  `get_package_prefix('mujoco_ros2_control')`) and check
  `lib/libmujoco.so.3.12.0` is present and `lib/libmujoco.so.3.9.0` is ABSENT, and/or that the
  plugin's `DT_NEEDED` is `libmujoco.so.3.12.0`. Prefer checking the shipped `lib/libmujoco.so.*`
  filename (no ELF parsing needed) + the absence of the 3.9 lib.
- Bump `scripts/test_baseline.json` (the integrity guard ratchets it automatically on a green
  run — commit the bump).

### R4 — Keep the #138 mass retune (0.16 kg); do NOT revert to 0.01 kg
`_ROLLER_MASS = 0.16` is an interior value delivering ~1.0x on both 3.9 and 3.12. Reverting to
0.01 kg re-introduces the exact value that wedged the 3.9 solver, and proving "3.12 holds
0.01 kg faithfully through the ROS chain" is a separate, risky experiment outside this PR's
scope (version alignment). Keep 0.16 kg. Record in `implementation.md` + `decisions.md` that
the retune is now orthogonal to the version split, with the OPTIONAL follow-up noted: test
0.01 kg on the now-unified 3.12 path. Do NOT open an issue (the manager posts follow-ups).

### R5 — Verification is build-time + structural, not just pytest
Acceptance #1/#2/#3 are structural. The implementer must capture hard evidence in
`implementation.md`:
- `objdump -p install/mujoco_ros2_control/lib/libmujoco_system_plugins.so | grep NEEDED` ->
  `libmujoco.so.3.12.0` (and NOT `3.9.0`).
- `ls install/mujoco_ros2_control/lib/libmujoco.so.*` shows `3.12.0` only (fresh install).
- `git status` shows the durable change in tracked files only (robot.repos/.gitignore/pixi.toml/
  decisions.md + test) and `src/mujoco_ros2_control/` untouched (gitignored).

### R6 — Commit discipline + doc hygiene
- Commit in small green increments. Run `pixi run build` + `pixi run test` locally before
  handoff. Commit the `scripts/test_baseline.json` ratchet bump.
- `docs/features/i139-mujoco-312-alignment/` stays **UNTRACKED** (do not `git add` it) — it is
  for review; Sisyphus deletes it at merge.
- Never read STL/mesh bytes into context.
- Do not re-run `vcs import` in a way that clobbers anything (it is idempotent; after adding
  `mujoco` to robot.repos, re-running it fetches `src/mujoco/`).

## Dispatch
implementer (flash) -> red-team (flash) -> test-runner (flash). N+1 red-team<->fix rounds.
No merge — report "ready" with the PR link. Fallback (fork) is NOT to be taken silently;
escalate to the manager first.

---

## Re-rulings (post-implementer, before red-team)

The implementer escalated two reasoning errors in R1 and deviated in owned tracked
files only (no vendored edit, no fork). Both deviations ACCEPTED — they are necessary
for the ruled mechanism to actually work, and they keep us on upstream dfki-ric.

- **R1 was wrong that the disappearing `_deps/mujoco-src/*` include dirs are "benign".**
  Empirically false. Two consequences, each fixed in owned files:
  1. `src/mujoco/` is discovered by colcon as a workspace package `mujoco`, which (a) builds
     standalone with bundled glfw -> GL/gl.h failure, and (b) shadows the conda `mujoco` that
     `robot_description`'s `<exec_depend>mujoco</exec_depend>` refers to, breaking every colcon
     verb (including the `colcon test` inside `pixi run test`). Fix: a new `ensure-mujoco-ignore`
     pixi task (depends-on of both `build` and `test`) that `touch src/mujoco/COLCON_IGNORE` when
     the checkout exists. `--packages-ignore mujoco` alone was tried and insufficient (build verb only).
  2. The vendored global `include_directories(.../_deps/mujoco-src/simulate)` vanishes under the
     override, and `simulate_gui`'s dep on `mujoco::platform_ui_adapter` is PRIVATE, so
     `mujoco_ros2_control_plugin.cpp` -> `simulate_gui.hpp` -> `<simulate.h>` fails. Fix:
     `-DCMAKE_CXX_FLAGS=-I$PWD/src/mujoco/simulate` appended to the build task (restores exactly the
     include path the vendored CMakeLists intended, repointed at the override source).
- **pixi expands a plain `$PWD` but NOT a braced `${PWD}`** (verified) — R1's `${PWD}` would be
  passed literally. Corrected to `$PWD` in the build task.
- **`scripts/tests/test_provisioning.py`** pinned `test.depends-on == ['check-provisioning']`
  exactly; relaxed to "the guard is first" (its actual intent) so the added marker task is allowed.
- Red-team should verify each of these empirically (see dispatch).
