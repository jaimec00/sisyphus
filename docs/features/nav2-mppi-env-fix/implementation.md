# implementation — #127: Nav2/MPPI drives the base backwards (env fix)

Issue #127. Rulings E1/C1/C2/T1/R1 in `status.md` are the spec; every one is
implemented as written, no deviations (see "Deviations" at the end — none).
Everything below was **run** on the laptop node `olivia`, not recalled.

## The bug, in one paragraph

After #124/#125 the base drove **exactly backwards** under a `NavigateToPose`
goal: even a trivially straight-ahead, un-yawed +x goal drove the base to
x ≈ −3.2 m, with MPPI pinning `vx` at its reverse limit and oscillating `wz`
through ±0.6 rad/s. `/odom`, the `odom -> base_link` TF, wheel tracking, the
map extent and the open-loop `/cmd_vel` path each measured correct — the
defect was in the Nav2 controller layer, not the plant.

## Root cause: a broken prebuilt MPPI binary (E1)

The conda `ros-jazzy-nav2-mppi-controller` 1.3.12 binary in this environment
was compiled against a **mismatched `xtensor 0.23.9` + `xsimd 14.3.0` pair**.
xtensor 0.23.9's `xtensor_simd.hpp` calls the removed old xsimd API
(`xsimd::aligned_allocator<T,A>`, `xsimd::set_simd/load_simd/store_simd`,
`XSIMD_DEFAULT_ALIGNMENT`) — none of which exist in xsimd 14.3.0. The MPPI
optimizer's SIMD rollouts are therefore numerically wrong, and the controller
steers the base opposite the goal. Because xtensor/xsimd are **header-only**,
re-pinning the env cannot repair the already-compiled `.so`: the package has to
be **rebuilt**.

## The fix

### 1. Vendored `nav2_mppi_controller` 1.3.12, XSIMD off (E1)

`src/nav2_mppi_controller/` — a verbatim copy of the `nav2_mppi_controller/`
subdirectory from the navigation2 **1.3.12** tag (`/tmp/nav2-src`, a full
navigation2 checkout at tag 1.3.12), with a four-line patch to
`CMakeLists.txt` only (no `.cpp`/`.hpp` changed):

- `add_definitions(-DXTENSOR_ENABLE_XSIMD)` / `(-DXTENSOR_USE_XSIMD)`
  commented out,
- `set(XTENSOR_USE_XSIMD 1)` → `0`,
- `xtensor::use_xsimd` dropped from the `target_link_libraries` line.

Scalar xtensor is numerically **identical** to a correctly-compiled SIMD build
(it simply skips the vectorized path), and at MPPI's problem size the speed
difference is irrelevant. The source-built package shadows the broken conda
binary through the colcon overlay (verified: `install/nav2_mppi_controller/`
exists after the build, and the committed config changes only make sense with
the fixed controller).

`src/nav2_mppi_controller/VENDORED.md` documents the provenance (upstream,
tag, the local patch, why in-tree rather than `robot.repos` — nav2 is a
monorepo subdir vcstool cannot fetch, and the package needs a local patch) and
why XSIMD is off. It is also the marker the test guard keys off (below).

### 2. `nav2.yaml` config fixes (C1/C2)

`src/robot_nav/params/nav2.yaml`, two values only:

| key | before | after | why |
|---|---|---|---|
| `min_y_velocity_threshold` | `0.5` | `0.001` | `0.5` zeroed the whole `vy` channel (`vy_max = 0.30`), so the lateral channel never commanded motion |
| `PreferForwardCritic.enabled` | `false` | `true` | `false` let MPPI pivot/stall instead of preferring forward motion |

### 3. Test-integrity guard: vendored packages are "unowned" (E1)

`scripts/check_test_integrity.py` classified a package as `unowned` (exempt
from the "implementation code must have tests" rule and the ratchet) **iff its
`package.xml` is not git-tracked**. Our vendored package *is* tracked (it is a
plain in-tree copy, not a vcs-imported one), so it kept getting audited and
would have failed the guard.

`discover_packages` now also treats a package directory containing a
**`VENDORED.md`** marker as unowned, even when git tracks it. The exemption is
deliberately narrow (only the exact `VENDORED.md` name — `VENDORED`,
`vendored.md` etc. do not exempt) and self-documenting: exempting a first-party
package would mean adding a file whose own contents claim the package is
third-party, a visible and reviewable act. `COLCON_IGNORE`/`.gitignore` remain
powerless, as before. New tests in `scripts/tests/test_audit.py` cover: a
tracked marked package is exempt; near-miss marker names are not; the marker
works outside a git work tree; and the report still names the exempted
package.

### 4. Closed-loop acceptance test re-enabled (T1)

`src/robot_bringup/test/test_pr2_navigate.py`,
`test_base_converges_on_a_lateral_navigate_to_pose_goal`: the terminal
`pytest.skip(...)` is replaced with real assertions. The test now:

- guards on `_have_package('mujoco_ros2_control')` (same as the open-loop
  test) instead of skipping unconditionally;
- runs `_goal_probe(NAV2_DOMAIN_ID + 2)` — a distinct domain from the
  open-loop probes (base and +1);
- asserts `succeeded` (Nav2 `STATUS_SUCCEEDED`, which already encodes
  xy < 0.10 m AND |yaw| < 0.15 rad via the goal checker);
- asserts ground-truth xy convergence:
  `hypot(dx - GOAL_X, dy - GOAL_Y) < GOAL_XY_TOLERANCE` (0.10 m);
- asserts ground-truth yaw convergence:
  `|wrap(dyaw - GOAL_YAW)| < GOAL_YAW_TOLERANCE` (0.15 rad), using an inline
  `atan2(sin, cos)` wrap because the module-level `_wrap` helper does not exist
  (it is nested inside the two probe workers).

The `_goal_probe_worker` per-sample incremental-yaw fix from #129 is confirmed
present (the `_accumulate` closure), so `dyaw` is a correctly accumulated turn
rather than an aliased `end − start`.

The docstring is rewritten to describe the real assertion and to note that
this closes the #127 controller-side blocker; the obsolete "still skipped"
text and the "plant does not compose" framing (already refuted in
`docs/features/vx-wz-composition/`) are gone.

## Verification performed

- `pixi run build`: green after the documented mujoco-header workaround (see
  below). `nav2_mppi_controller` builds into `install/nav2_mppi_controller/`,
  shadowing the conda binary. Confirmed the installed `.so` carries **no**
  throwaway debug instrumentation (`strings … | grep -c I127` = 0; the
  `/tmp/nav2-src` working tree had uncommitted I127DBG/I127COST logging in
  `critic_manager.cpp`/`optimizer.cpp`, restored to the pristine 1.3.12 tag
  before vendoring — logging only, no behavioural change, but not something to
  ship).
- `scripts/tests` (the guard's own suite): **199 passed**, including the four
  new vendored-marker tests and the existing "a first-party package cannot opt
  out with a marker file" test.
- Closed-loop probe (an adapted `/tmp/i127_probe.py` that also reports
  accumulated yaw), run against the newly built tree. Results below.

### Probe results

**(a) Straight +x goal — DIRECTION FIXED (the #127 bug).** Three runs, all
with `cmd_vel vx ∈ [0, +0.30]` (never reverse) and positive ground-truth `dx`:

| run | goal | dx | dy | dyaw | succeeded |
|---|---|---|---|---|---|
| probe-forward | (1.0, 0, 0) | **+0.625** | +0.023 | +0.162 | False |
| probe-forward2 | (1.0, 0, 0) | **+0.944** | −0.105 | −0.495 | False |
| throwaway probe (same tree) | (1.0, 0, 0) | **+1.108** | +0.427 | — | False |

Before the fix the same probe measured dx = −2.1 … −3.3 (drive **backwards**,
throwaway's `fix1`–`fix5`/`baseline` results). The base now drives forward —
#127's actual defect is fixed.

**(b) Acceptance goal (0.60, −0.45, yaw −1.00) — xy converges, yaw
does not, intermittently.**

A real single-test invocation (`pytest ...::test_base_converges_on_a_lateral_
navigate_to_pose_goal`, install sourced, domain 124) ran the re-enabled test
**end to end** in 143 s and failed on the **last** assertion only:

- `assert succeeded` — **PASSED** (Nav2 reported `STATUS_SUCCEEDED`)
- `assert xy_error < 0.10` — **PASSED** (ground-truth xy converged)
- `assert yaw_error < 0.15` — **FAILED**: ended `dyaw = −0.756` vs goal −1.00,
  yaw err **0.244 rad**.

Standalone probes on the new tree (my adapted probe) gave dx=+0.888/dy=−0.099
(failed) and dx=+0.843/dy=−0.496 (dy within 0.046 of goal, dx overshoot, yaw
+3.485 → failed). The throwaway's own history shows the **xy** half converging
on this fixed controller (`accept2`: xy err 0.089; `final`: xy err 0.068 — the
ruling's two "reference pass" numbers), while yaw was the flaky half.

This is ruling **R1** exactly: the env fix is correct (direction fixed, xy
converges, `STATUS_SUCCEEDED` reached), and the residual is an **intermittent
yaw** gap — a genuine MPPI tuning matter, not env numerics. Per T1 the strict
assertions (succeeded + xy + yaw) are landed as written; the single sampled run
above is **red on yaw**, so the >=5-run batch and the keep-vs-tune decision are
**red-team's** (R1). If yaw stays intermittent, R1's sanctioned fallback is to
assert `succeeded` + xy and document the yaw gap honestly — never merge a flaky
test.

**A separate plant finding (NOT #127, pre-existing).** A diagnostic that holds
a *direct* `/cmd_vel` at a constant `vx = 0.2 m/s` for 25 s shows the wheel
command bridge is healthy — `/base_velocity_controller/commands` stays at a
fresh `(3.464, 0, −3.464)` throughout — but the ground-truth base translates
only ~0.6 m in 25 s (~0.024 m/s, ~12 % of commanded) and **creeps laterally**
(y: 0 → 0.56 m under a pure +vx command). `/odom` and the ground-truth body
pose agree throughout, so this is a **plant/contact** effect (the rim-roller
omniwheel model of #125/#128), not a controller or localization defect. It is
independent of this PR (nothing here touches the plant) and is the mechanical
reason a long closed-loop goal struggles to hold yaw: the base does not
actually deliver sustained commanded velocity. Worth a separate issue.

### Build workaround (mujoco headers)

`mujoco_ros2_control`'s vendored mujoco **3.9.0** source builds its `simulate`
target against headers, but the compiler picks up the conda env's mujoco
**3.12.0** headers from `.pixi/envs/default/include/mujoco` first, and 3.12's
`mjv_moveCamera` signature no longer matches `simulate.cc`. Workaround (per the
existing PR8b note): temporarily replace that include dir with a symlink to the
fetched 3.9.0 headers, build, then restore the conda headers. Done here by hand
(`mujoco` → `mujoco.i127bak` while building). This is a **host/build-env**
workaround, not a repo change; `MUJOCO_BUILD_EXAMPLES=OFF` from
`colcon_defaults.yaml` already handles the examples half.

## Open question (R1) — deferred to red-team

The throwaway (scalar rebuild + config) still showed **~50 % intermittent yaw
failure** on the acceptance goal (final yaws +1.54 / +2.94 / −3.06 vs goal
−1.00). Since the scalar rebuild is numerically correct, the residual is most
likely a genuine MPPI **yaw-tuning** gap, not env numerics. The >= 5-run clean
batch that decides this is **red-team's** job (the ruling defers it); this
implementer only ran the quick probes. If yaw proves reliable in the batch,
the assertions as written hold; if it stays intermittent, the ruling's options
(tune MPPI yaw critics further, or keep xy + `succeeded` and document the yaw
gap honestly) apply. Never merge a flaky test.

## Deviations from the rulings

None.
