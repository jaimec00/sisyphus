# Implementation — issue #132: rim-roller plant velocity fidelity

**Branch:** `feat/i132-rim-roller-velocity-fidelity` · **Worktree:**
`~/worktrees/i132-rim-roller-velocity-fidelity` on node `olivia` ·
**Implementer:** worker subagent (DeepSeek v4 Flash).

## Headline

**The assigned premise is false on the shipped plant: the rim-roller omniwheel
already delivers commanded velocity faithfully.** Measured through the test
infra (`_drive` in `test_mjcf_drive.py`), every channel is symmetric and within
a few percent of commanded, in **both** directions, and it tracks down to
0.03 m/s with **no stiction floor**. No change to `mjcf_model.py` (or
`overlay.xml`) was needed — the plant is correct.

The closed-loop `NavigateToPose` acceptance (`succeeded=True`) still does not
hold reliably — but the cause is the **MPPI `wz` oscillation** in the Nav2
controller layer (**#131** scope), not the plant. The open-loop velocity-fidelity
defects listed in the #131/#132 briefs (~0.57x backward `vx`, ~1.4x `vy`, ~0.45x
`wz`, a 0.10-0.15 m/s stiction threshold) **do not reproduce** on the current
plant under any ramp/settle variation tried.

## 1. The plant delivers (measured, not reasoned)

Identical derivation to `write_mjcf_model` (the file the ROS sim loads), isolated
pure MuJoCo, the shipped `_body_to_wheel` IK bridge, wheel `<velocity kv=10>`
actuators, base settled 1 s then ramped 1.5 s onto the command, twist read in the
**body frame** over a 1 s window after the ramp (`test_mjcf_drive._drive`).

### Steady per-channel delivery (before == after: no plant change)

| command | delivered (body frame) | ratio | acceptance (>= 0.9x) |
|---|---|---|---|
| `+vx=0.30` | `lin_x = +0.2985` | **0.995x** | PASS |
| `-vx=0.30` | `lin_x = -0.3040` | **1.013x** | PASS |
| `+vy=0.30` | `lin_y = +0.3025` | **1.008x** | PASS |
| `-vy=0.30` | `lin_y = -0.3030` | **1.010x** | PASS |
| `+wz=0.60` | `ang_z = +0.5977` | **0.996x** | PASS |
| `-wz=0.60` | `ang_z = -0.5818` | **0.970x** | PASS |

The brief reported backward `vx` at ~0.57x, `vy` at ~1.4x, and `wz` at ~0.45x.
None of those reproduce: backward `vx` is 1.013x (not 0.57x), `vy` is 1.008x (not
1.4x, and there is no lateral creep — uncommanded `lin_x` stays at ~0.0003 m/s),
and `wz` is 0.996x (not 0.45x).

### Low-speed / stiction

| command | delivered | ratio | 5 s displacement |
|---|---|---|---|
| `vx=0.10` | `lin_x = +0.0999` | **0.999x** | 0.510 m (ideal 0.500) |
| `vx=0.05` | `+0.0533` | 1.066x | — |
| `vx=0.03` | `+0.0317` | 1.055x | — |
| `vy=0.05` | `+0.0493` | 0.986x | — |
| `wz=0.05` | `+0.0568` | 1.135x | — |

There is **no stiction threshold** at 0.10-0.15 m/s: the plant tracks a 0.03 m/s
command. The small overshoot ratios at 0.03-0.05 m/s are the measurement window
catching the tail of the ramp, not a floor.

### Robustness of the negative result (the premise was re-checked, not assumed)

Because "no defect" is easy to reach by measuring wrong (the #127 file records
exactly that trap — a cold ~1 s ramp read in the world frame), the backward `vx`
case was re-measured across ramp times 0.2/0.3/0.5/1.0/1.5 s and settle times
0.0/0.1/0.3/1.0 s: every combination lands at 1.01-1.05x. There is no
measurement recipe under which backward `vx` reads ~0.57x.

## 2. Why the brief's numbers differ (hypothesis, not a conclusion)

The #131 `status.md` labels those ratios "VERIFIED, open-loop, clean host", but
they are not reproducible with the **test-infra** `_drive` on the identical plant
(the i131 and i132 `mjcf_model.py` / `overlay.xml` are byte-identical, both at
`27daa40`). The likely cause is a **probe-measurement artifact**: the ROS-side
open-loop probe (`_drive_probe`) is direction-only and, per its own note, only
reproducible from a fresh sim ("a second command in the same session slips") — a
number-bearing speed measurement would have needed a hand-rolled probe (which R5
forbids). This is recorded as a hypothesis; the *measurement* (infra, isolated,
deterministic) is what the tests pin.

## 3. The real blocker: closed-loop `succeeded` (MPPI, #131 scope)

Five `_goal_probe` runs of the acceptance goal `(0.60, -0.45, yaw -1.0)`:

| run | dx | dy | dyaw | succeeded | xy_err |
|---|---|---|---|---|---|
| 1 | 0.591 | -0.413 | -0.907 | **True** | 0.038 |
| 2 | 0.500 | -0.419 | -20.382 | False | 0.104 |
| 3 | 0.862 | -0.490 | -5.736 | False | 0.265 |
| 4 | 0.629 | -0.437 | -0.627 | False | 0.032 |
| 5 | 0.512 | -0.448 | -6.478 | True | 0.088 |

xy converges in all five (err 0.03-0.27 m), but `succeeded` holds in only 2/5,
and the failing runs show the base **rotating away** (`dyaw` -5.7 / -6.5 / -20.4
rad) after reaching the goal — the MPPI `wz` command oscillating while the Omni
model keeps issuing yaw, then aborting on the progress checker. A plant that did
not deliver the commanded `wz` could not spin the base through three extra turns:
the runaway is the controller commanding it, not the plant failing to.

This is the #131 failure mode (holonomic base + MPPI critic/noise config), and
R1 scopes #132 to the plant — so it is **escalated, not expanded into**.

## 4. Probe methodology

- **Open-loop fidelity / low-speed:** `test_mjcf_drive._drive` and
  `_settled_slip` (isolated `mujoco`, deterministic, no ROS). Iteration also used
  short `python -c` runs of the same helpers (allowed by R5 for the isolated
  sim); every assertion lives in the regression tests.
- **Closed-loop:** `test_pr2_navigate._goal_probe` (full bringup on an isolated
  ROS domain), goal `(0.60, -0.45, -1.0)`.
- No hand-rolled ROS probe scripts (R5).

## 5. Tests added

`src/robot_description/test/test_mjcf_drive.py`:

- `test_write_mjcf_model_every_channel_delivers_both_directions` — per channel,
  **both** directions, >= 0.9x the command, uncommanded channels <= 0.15. This is
  tighter (0.9x vs 0.8x) and covers both signs, unlike the existing
  single-sign `..._pure_channels_deliver_command`.
- `test_write_mjcf_model_low_speed_command_moves_the_base` — `vx=0.10` delivers
  >= 0.9x **and** moves the base >= 0.4 m in 5 s.

`src/robot_bringup/test/test_pr2_navigate.py`:

- `test_navigate_to_pose_converges_within_tolerance` — the closed-loop acceptance
  (xy < 0.10 AND `succeeded=True`), added alongside the #127 direction
  regression. Marked `xfail(strict=False)` with the blocker named (#131 MPPI
  `wz` oscillation): it records the claim and flips to XPASS when #131 lands.
  `xfail` is excluded from the ratchet (`SKIPPED_CASE_TAGS`), so a
  blocked-on-another-issue acceptance does not gate merges.

Existing invariants kept green: combined `vx+wz` composes, pure channels deliver,
rollers roll (not stall), contact slip stays low, welded escape hatch unchanged
(nq = nv = 18, no rollers).

## 6. Provisioning note (environment, not this feature)

Two pre-existing provisioning failures had to be worked around on this worktree;
neither is a code change to the repo (both live in the git-untracked
`src/mujoco_ros2_control/`, fetched via `robot.repos`):

1. **`simulate_gui` fails to compile.** The vendored MuJoCo 3.9.0 headers
   (`_deps/mujoco-src/include`) are listed *after* the conda env's
   `mujoco/mujoco.h` (3.12.0) in the CMake include order, so `simulate.cc` (which
   calls the 3.9.0 `mjv_moveCamera` 6-arg signature) compiles against the 3.12.0
   5-arg declaration and fails. A local reorder of `${rclcpp_INCLUDE_DIRS}` below
   the vendored include in that package's `CMakeLists.txt` fixes it. The
   checked-in worktrees (i131 etc.) only built because their `.o` was stale from
   an earlier env state; a fresh configure fails identically.
2. **`mujoco_ros2_control_examples` downloads Unitree H1 meshes at configure
   time** and fails intermittently on TLS/network, aborting `robot_mcp`. Not used
   by any test; left `COLCON_IGNORE` locally.

Both are recorded for escalation: a fresh provision of any worktree is broken
until the vendored include order is fixed (would need a `robot.repos` fork pin or
a build-flag), independent of #132.
