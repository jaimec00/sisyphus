# Feature status — issue #127: combined vx+wz does not compose (plant defect)

**Worktree:** `~/worktrees/i127-vx-wz-composition` on node `olivia`
(branch `feat/i127-vx-wz-composition`, cut from `origin/main` @ `0e20f25` = #128).

**Manager:** Sisyphus (worktree manager, `deepseek-v4-pro`).

## The brief (one line)
After #125 fixed the pure channels (vx 0.98x, vy 1.02x, wz +1.04x correct sign),
a **combined** `vx + wz` wheel command (and any coupled/off-axis twist) does NOT
compose: the base moves far off the commanded direction/speed (measured dx 0.17x
+ spurious dy 0.85 for `vx=0.3+wz=0.6`), so closed-loop Nav2 `NavigateToPose`
cannot converge (goal (0.60,-0.45,-1.00) diverges to x ~ -3.25 m, xy error
~3.9 m vs 0.10 m tolerance). This is a **plant** defect (the IK bridge
`body_to_wheel` is linear/correct; the pre-#125 plain-cylinder plant also fails
`vx+wz`), the likely mechanism being the rim rollers **slip/whirl** (roller joint
|qvel| ~260-325 rad/s during ALL commands, including pure `+vx` where a gripping
roller should be ~0) instead of gripping.

**Scope of this run = option A:** tune the roller contact so the rollers **grip on
roll** and only **slip laterally**, so `vx+wz` composes.

## Probing findings (what I read, grounded in the source)

- The roller model (#125) is in `robot_description/robot_description/mjcf_model.py`
  `_add_rim_rollers` / `_roller_body_xml` / `_shrink_hub_radius`, applied only to
  `write_mjcf_model` when `base_free_joint=True`. N=8 capsule rollers per wheel,
  each a child body on a free hinge whose axis is the rim **rolling-tangent**
  `d = (-sin th, cos th, 0)` (the wheel-link frame: +z = spin axis radially outward,
  rim in local xy). Roller barrel: capsule, cross-section radius `_ROLLER_RADIUS=0.007`,
  half-length 0.01; centre radius `_ROLLER_CENTER_RADIUS=0.043` (so the outer
  surface is back at `wheel_radius=0.05`); hub shrunk to `_HUB_RADIUS=0.040` with
  contype/conaffinity=0 (never contacts). Roller mass 0.01 kg, small inertia.
- The roller geoms carry **no** per-geom friction/solref/solimp/condim — they
  inherit the overlay `<default>` geom `friction="1.0 0.4 0.02" solref="0.01 1.0"`
  (condim defaults to 3 = isotropic). The floor plane sets `condim="3"
  friction="1.0 0.4 0.02" solref="0.02 1.0" priority="1"`.
- The roller hinges are **free** (no `frictionloss`, no `damping`, no `armature`).
  A free hinge + low-inertia roller is the whirl mechanism: any transient torque
  spins the roller up (|qvel| hundreds of rad/s) and nothing slows it.
- IK bridge (`robot_nav/robot_nav/omni_base_controller.py`): `body_to_wheel` is the
  linear LeRobot matrix; `WHEEL_SIGN=-1.0` (translational) / `WZ_SIGN=-1.0`
  (rotational) are the #125-calibrated correct values. **Do not touch**.
- Acceptance test `robot_bringup/test/test_pr2_navigate.py`:
  `test_base_converges_on_a_lateral_navigate_to_pose_goal` is currently `pytest.skip`ped
  ("deferred to #127"). `_goal_probe_worker` (~line 870) still uses the single
  `_wrap(end_yaw - start_yaw)` pattern — the yaw-aliasing bug #125 already fixed in
  `_drive_probe_worker` (issue comment 5755686831). Re-enabling the closed-loop test
  without fixing it will re-introduce the +279deg->-81deg sign flip.

## Manager rulings

### R1 — Scope: option A, sim-path-only, plant only.
Tune the roller contact. Do NOT touch: `body_to_wheel` (linear, correct),
`WHEEL_SIGN`/`WZ_SIGN` (correct), the welded escape hatch (`base_free_joint=False`),
`load_mjcf_model` (bare), `load_mjcf_model_with_scene` (in-process backend), or the
URDF (D29 gate parses the URDF wheel as a plain cylinder). All changes live in
`mjcf_model.py` (roller/floor/overlay contact params) + tests. Any change that
would violate a binding invariant is an **escalation**, not a silent edit.

### R2 — Target mechanism: grip on roll, slip laterally.
The rollers must end up **gripping** in the rolling direction (roller hinge |qvel|
~0 during a pure `+vx` drive) and **slipping laterally** (non-zero roller qvel only
under lateral/rotational components). The 260-325 rad/s whirl during pure `+vx` is
the defect to remove. Diagnose by reading roller-hinge `data.qvel` in a pure-sim
probe, not by reasoning alone.

### R3 — Knobs to probe (empirically, in this order), record every result.
1. **Roller-hinge `frictionloss`** (and/or `damping`, `armature`) on the roller
   hinge joint — models bearing friction; directly damps free whirl. Start small
   (e.g. 1e-3) and sweep; too large will also resist the *wanted* lateral slip, so
   watch that pure `+vy` still translates (~1.0x, no yaw).
2. **Anisotropic friction** — `condim="6"` + 5-element `friction` on the roller geom
   and/or floor, so the rolling direction grips (high) while the axle/lateral
   direction slips (low). This is the "grip on roll, slip laterally" contact-model
   fix if isotropic friction is what lets the rollers whirl.
3. **`solref`/`solimp`** stiffness on the roller/floor contact — stiffer = firmer
   forward grip, less contact chatter during roller-passing.
4. **Roller geometry/count** — N (baseline 8; #125 saw N=24 jam vx, keep < 24),
   `_ROLLER_RADIUS`, capsule vs sphere, half-length. Only if 1-3 alone do not compose.
The final config is whatever the probe shows composes; record before/after numbers
and the exact values in `implementation.md`. `body_to_wheel`/signs stay untouched.

### R4 — Acceptance (open-loop, pure-sim) — `vx+wz` composes.
Measure in the **isolated pure-sim** probe (derived MJCF, real velocity actuators,
base settled ~1 s, read `base_link` pose + roller `qvel`), for a combined command
`vx=0.3, wz=0.6` over a short window (~2 s, before a full reversal):
- forward translation **not scrubbed** (body-frame forward speed >= ~0.7x commanded,
  i.e. the base does not stall or slide sideways); and
- rotation **preserved** (yaw rate >= ~0.7x commanded, correct sign); and
- **no spurious lateral drift** (the lateral motion is the commanded rotation's
  natural arc, not an off-axis slide).
Plus the grip check: pure `+vx=0.3` leaves the roller hinges at |qvel| ~ 0 (order
of magnitude below the 260-325 rad/s whirl, not tens of rad/s).

### R5 — Regression tests (pure-sim, deterministic).
Add a dynamics test to `src/robot_description/test/test_mjcf_drive.py` (same file as
#125's structure tests) that, in pure sim, (a) drives pure `+vx` and asserts the
roller hinges stay ~0 (grip), and (b) drives `vx+wz` and asserts the base translates
~commanded direction/speed and rotates ~commanded with no large off-axis slide.
Deterministic: fixed derived model + fixed commands + implicit integrator. Keep the
existing structure tests green; update only if counts/names legitimately change.

### R6 — Re-enable closed loop + fix the goal-probe yaw aliasing.
In `test_pr2_navigate.py`: (a) drop the `pytest.skip` in
`test_base_converges_on_a_lateral_navigate_to_pose_goal` (and its now-stale
"DEFERRED to #127" docstring — rewrite to the real claim); (b) fix
`_goal_probe_worker` to sum **per-sample** yaw deltas (the same `_wrap(next_yaw -
yaw)` accumulation `_drive_probe_worker` now uses) instead of the single
`_wrap(end_yaw - start_yaw)`, so a >pi yaw change does not alias. `WZ_SIGN=-1.0`
stays (the "inverted +wz" symptom was that aliasing, not a real sign defect).

### R7 — Ratchet the baseline.
If `robot_description` or `robot_bringup` test counts grow, bump
`scripts/test_baseline.json` in the same change (the `pixi run test` floor). The
test-runner will flag it; do it as part of the fix, not as a follow-up.

### R8 — Verification by command (record exact numbers in `implementation.md`).
Open-loop pure-sim table for `vx`, `vy`, `wz`, and `vx+wz` (before/after), the
roller-qvel grip numbers, and the closed-loop `NavigateToPose` result (converges
within xy 0.10 / yaw 0.15) with the goal-probe fix applied.

## Open questions / risk
- Whether `frictionloss` alone, anisotropic friction (`condim=6`), or a geometry
  change is what composes — genuinely unknown; the implementer probes, the red-team
  re-runs. This is why the red-team must run the sim, not reason about it.
- Determinism of the new pure-sim dynamics test (roller-passing can oscillate) — the
  implementer must pick thresholds with comfortable margin and prove the test is
  stable across a few runs.

## Build-environment note (pre-existing, out of scope)
The vendored `dfki-ric/mujoco_ros2_control @ f151b7d` needs the local (git-untracked)
CMakeLists header-shadow patch (prepend `_deps/mujoco-src/include` + `simulate`).
Re-applied to this worktree by the manager during provisioning (copied from the i125
worktree); not tracked in the robot repo.

## Dispatch plan
implementer -> red-team (run, don't reason) -> fix -> re-red-team (N+1) ->
test-runner (full `pixi run test` on `olivia`; ratchet baseline if counts grow) ->
squash-merge PR. Manager opens the PR; does not merge.

**No design fork requiring escalation.** Option A is a contact-tuning call within
manager authority; it changes no binding invariant (D29 URDF, D30 ROS-free backend,
D33/34 tracks, skill-API seam) and keeps the IK bridge + signs untouched.
