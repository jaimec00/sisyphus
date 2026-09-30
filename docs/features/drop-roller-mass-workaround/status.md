# status.md — i141: drop the #138 roller-mass workaround

Manager: worktree-manager (i141). Worktree: `/home/sisyphus/worktrees/i141-drop-roller-mass-workaround`
(branch `feat/i141-drop-roller-mass-workaround`, off `origin/main` @ c0568e4).

## Rulings (binding for the implementer; red-team, challenge these)

- **R1 — scope is the value + its doc-comment.** Change `_ROLLER_MASS` 0.16 ->
  0.01 in `src/robot_description/robot_description/mjcf_model.py:165`. Do NOT
  touch the inertia constants (`_ROLLER_I_AXIS`, `_ROLLER_I_TRANS`) — they are
  computed from `_ROLLER_MASS` and update automatically. Do NOT `git revert`.

- **R2 — update the stale comment.** The comment block above `_ROLLER_MASS`
  (`mjcf_model.py:146-164`) documents the #137 3.9 workaround and would be
  actively contradictory after the revert ("raised from 0.01" while the value IS
  0.01). Replace it with a concise, factual note: 0.01 kg is the physical value;
  the #138 0.16 kg workaround is dropped now that the ROS sim links MuJoCo 3.12.0
  (D38, issue #141). Keep it short; reference D38 and the R9 diagnosis.

- **R3 — no test edits.** The acceptance regression
  `test_base_delivers_commanded_vx_through_the_ros_chain` (0.10/0.14/0.20 m/s,
  >= 0.85x, |dyaw| <= 0.30) already pins exactly the acceptance criteria; leave
  its code untouched. Its *docstring* historical narrative (mentions the 3.9
  stall and the roller-mass fix) becomes mildly stale — that is a **NOTE**
  (surface as follow-up), not a blocker, and out of scope for a "plain value
  edit". The assert logic is version-agnostic and still correct.

- **R4 — no mitigation, no version change.** No vx/vy caps, no solver/friction
  retune to "help" the 0.01 kg case. If the 0.01 kg plant slips on 3.12, that is
  a genuine design fork -> STOP, escalate to the manager (do not keep 0.16).

## Open questions / escalations
**ESCALATED — design fork.** The "plain value edit" branch fails (see `red_team.md`):
0.01 kg slips on the 3.12 ROS chain (0.666x at 0.20 m/s) while holding on the
3.12 direct path (1.0x) and while 0.16 kg holds on the ROS chain (1.0x). The
#138 premise ("3.9-only solver delta") is incomplete. Fix direction undetermined.

## Loop state
- [x] Worktree created @ c0568e4 (latest origin/main).
- [x] Provisioned: `pixi install` + `pixi run install-openclaw` (OpenClaw 2026.9.6).
- [x] Build (with `src/mujoco` + `src/mujoco_ros2_control` @ pinned commits; 3.12.0 link verified).
- [x] Implement (value 0.16->0.01 + comment update; commits fca738e, f8d8fb2).
- [x] Fidelity regression (0.10/0.14/0.20 through ROS chain) — **0.20 m/s FAILS (0.666x)**.
- [x] Red-team / debug (read-only; direct-path + 0.16 kg + impratio probes) — `red_team.md`.
- [ ] Test-runner full `pixi run test` — **not run** (moot: acceptance branch failed).
- [ ] PR — **do NOT open/merge**; escalate instead.

## Wheel-qvel split probe (deciding measurement, 0.20 m/s +vx)

Read the wheel joint qvel through each path (scratch probe, untracked) to split
contact slip from a command/actuation drop. 0.01 kg roller, 3.464 rad/s is the
commanded wheel rate for left/right at 0.20 m/s (back wheel commanded 0).

| path | base delivered | left wheel qvel | right wheel qvel | wheels/commanded |
|---|---|---|---|---|
| ROS chain (mujoco.launch.py, /joint_states) | 0.495x (dx=0.396 m/4 s) | **+3.4640** rad/s | **-3.4641** rad/s | **1.000x** |
| DIRECT 3.12 (no ROS, mjData.qvel) | 1.007x (dx=0.805 m/4 s) | +3.4564 | -3.4529 | 0.998x |

**VERDICT: contact slip, not a command/actuation drop.** Through the ROS chain
the wheels reach the commanded 3.464 rad/s *exactly* (1.000x, peak 3.4654) while
the base body delivers only 0.495x — the wheel-ground contact is scrubbing, i.e.
the wheels spin at the commanded rate but the base does not advance
proportionally. The dfki-ric `mujoco_ros2_control` velocity-command path is NOT
attenuating the command. (Note: this runs base ratio 0.495x is worse than the

## Wheel-qvel split probe (deciding measurement, 0.20 m/s +vx)

Read the wheel joint qvel through each path (scratch probe, untracked) to split
contact slip from a command/actuation drop. 0.01 kg roller, 3.464 rad/s is the
commanded wheel rate for left/right at 0.20 m/s (back wheel commanded 0).

| path | base delivered | left wheel qvel | right wheel qvel | wheels/commanded |
|---|---|---|---|---|
| ROS chain (mujoco.launch.py, /joint_states) | 0.495x (dx=0.396 m/4 s) | **+3.4640** rad/s | **-3.4641** rad/s | **1.000x** |
| DIRECT 3.12 (no ROS, mjData.qvel) | 1.007x (dx=0.805 m/4 s) | +3.4564 | -3.4529 | 0.998x |

**VERDICT: contact slip, not a command/actuation drop.** Through the ROS chain
the wheels reach the commanded 3.464 rad/s *exactly* (1.000x, peak 3.4654) while
the base body delivers only 0.495x -- the wheel-ground contact is scrubbing, i.e.
the wheels spin at the commanded rate but the base does not advance
proportionally. The dfki-ric `mujoco_ros2_control` velocity-command path is NOT
attenuating the command. (Note: this run's base ratio 0.495x is worse than the
red-team's 0.666x -- run-to-run variance in the same partial-slip regime; the
wheel qvel is the deciding signal and it is clean either way.)

**Next fix direction implied:** the contact model / solver interaction with the
0.01 kg rim roller, not the ros2_control actuation path. The remaining gap vs
the direct path (which holds) is a runtime/config difference in how the ROS
chain steps the *same* MJCF (contact-solver warm-start / state / geomgroup /
step cadence). Next probe: dump per-step MuJoCo contact data (contact count,
roller tangent slip, actuator force) from inside the ROS-chain sim vs the direct
path at the same command, to find which runtime difference destabilises the
light-roller contact.

## Runtime-diff probe (mjModel.opt + control-cadence + step trace)

Scratch probes (untracked, `/tmp`): `i141_opt_probe.py`, `i141_ctrl_decimation_probe.py`,
`i141_step_trace.py`. Read the solver config the ROS-chain sim *actually* runs vs
the direct path, then tested the remaining runtime deltas.

**1. `mjModel.opt` is IDENTICAL.** Both paths load the *same* generated MJCF
(`write_mjcf_model()` output) with `MjModel.from_xml_path` in MuJoCo **3.12.0**:

| field | DIRECT | ROS chain |
|---|---|---|
| timestep | 0.002 (500 Hz) | 0.002 (500 Hz) |
| integrator | IMPLICIT | IMPLICIT |
| solver | NEWTON (2) | NEWTON (2) |
| iterations | 100 | 100 |
| noslip_iterations | 0 | 0 |
| impratio | 1.0 | 1.0 |
| cone | PYRAMIDAL | PYRAMIDAL |
| nq/nv/nu/nbody | 49/48/18/45 | 49/48/18/45 |
| wheel gainprm/biasprm | 10 / (0,0,-10) | 10 / (0,0,-10) |

The plugins **only** model override is

## Runtime-diff probe (mjModel.opt + control-cadence + step trace)

Scratch probes (untracked, `/tmp`): `i141_opt_probe.py`, `i141_ctrl_decimation_probe.py`,
`i141_step_trace.py`. Read the solver config the ROS-chain sim *actually* runs vs
the direct path, then tested the remaining runtime deltas.

**1. `mjModel.opt` is IDENTICAL.** Both paths load the *same* generated MJCF
(`write_mjcf_model()` output) with `MjModel.from_xml_path` in MuJoCo **3.12.0**:

| field | DIRECT | ROS chain |
|---|---|---|
| timestep | 0.002 (500 Hz) | 0.002 (500 Hz) |
| integrator | IMPLICIT | IMPLICIT |
| solver | NEWTON (2) | NEWTON (2) |
| iterations | 100 | 100 |
| noslip_iterations | 0 | 0 |
| impratio | 1.0 | 1.0 |
| cone | PYRAMIDAL | PYRAMIDAL |
| nq/nv/nu/nbody | 49/48/18/45 | 49/48/18/45 |
| wheel gainprm/biasprm | 10 / (0,0,-10) | 10 / (0,0,-10) |

The plugin's **only** model override is `opt.timestep = 1/simulation_frequency`
(`mujoco_ros2_control_plugin.cpp:350`), and the launch sets
`simulation_frequency: 500.0` -> 0.002 s, so even that is a no-op. No solver /
integrator / warm-start / condim / friction difference exists. **The `opt` paths
are byte-identical in effect.**

**2. Control-refresh decimation is NOT the trigger.** ROS chain runs CM
`update_rate: 100` (0.01 s) while the sim steps at 500 Hz (0.002 s): the plugin's
`updateControllersAtCurrentTime()` gates `write()` to every 5th step, so the wheel
velocity actuator fires only 1 step in 5 (direct probe rewrites `ctrl` every step).
Reproduced that on the direct path: holding `ctrl` every step vs every 5th step
gives **identical** 1.002x (dx=0.8015 m both). Decimation is exonerated.

**3. Step trace through the ROS chain (0.20 m/s, 0.01 kg):** wheels hold the
commanded rate (`wl=+3.4574`, `wr=-3.4594`, back `+0.0001` mean; commanded
+/-3.464) while `base_vx` mean = **+0.0084 m/s (~0.04x)** with stddev 0.025 m/s
and frequent *sign reversals* (-0.03 .. +0.06). The wheel joints reach the full
commanded rate but the base does not advance coherently -- the roller-floor
contact scrubs, so the wheel spin does not translate into base motion.

**VERDICT -- the residual difference is NOT in `mjModel.opt` and NOT in control
cadence.** Both paths compile the same XML with the same solver at the same
timestep; the direct path holds 1.002x and the ROS chain scrubs. The only other
runtime inputs that differ are the *initial state* and *contact environment*:

- **Initial state / warm-start.** The plugin calls `mj_resetData` (no keyframe),
  `mj_forward`, then **one `mj_step`** before the controllers come up
  (`mujoco_ros2_control_plugin.cpp:121-123`), then runs `read/update/write`
  housekeeping cycles *before* the first commanded step. The direct probe settles
  0.5 s with `ctrl=0`. A different pre-drive pose / penetration / contact
  warm-start on a 0.01 kg roller decides whether the contact stays formed.
- **Biased-start geometry.** The bringup drives the base from a *current* pose
  and the roller angle distribution at contact differs from the direct probe's
  fresh-reset pose; a light roller loses contact on a bad initial angle and never
  recovers (wheels then free-spin at commanded rate -- exactly the trace above).

**Fix direction implied (not a config-only fix).** Aligning solver params is a
no-op (already aligned). The leverage is at the contact, for the 0.01 kg roller:
`condim` (3 -> 4/6, add rolling/torsional friction), explicit `solref`/`solimp`
on the roller geoms, `noslip_iterations` / `impratio`, or a roller joint
damping/`armature` term to keep the light contact formed. All are R4-forbidden
mitigations, so this stays a **design fork -> escalate**, not a silent retune.

**Next probe:** dump `data.qpos`/`qvel`/`qacc` + `data.ncon` + per-contact
`mj_contactForce` at startup and over the first ~50 steps inside the ROS chain
(vs direct) to pin whether the roller contact is formed at t=0 -- that isolates
"warm-start/initial state" from "solver never converges".

---

## Diagnostic probe (READ-ONLY, 2026-09-29) — warm-start hypothesis REFUTED; failure is a base TIP-OVER

**Method.** The plugin exposes NO contact topic/service (verified from
: only GetBodyState / SetBodyPose /
StepSimulation / play-pause-reset), so mjData of the live chain is unreadable.
Instead I replicated the plugin's EXACT startup ( -> 
-> ONE ; ) in a direct harness
and compared per-step contact evolution against the settled-0.5 s path, both
driving the same  =  rad/s.

**Result 1 — startup sequence is NOT the cause.** Startup-replica vs settled,
sustained window 1.5-4.0 s:
 (both ~1.0x). Deterministic across
reruns. The startup sequence does NOT reproduce the ROS ~0.5x. Also exonerated:
arm-actuator pose ( holds reset pose in ROS;
replica at ctrl=0 vs held-at-qpos0 -> 0.994 / 0.999 / 1.003 / 1.004x) and
control cadence (refresh every 5th step == every step).

**Result 2 — the MJCF is provably identical.**  text
re-materialized at runtime () is
**byte-identical** to the direct harness's model (sha match, both 25081 B). The
plugin's only model override is  (no-op). So the divergence is NOT
model/solver/startup.

**Result 3 — the live ROS chain TIPS THE BASE OVER.** Ground-truth
 trace at 0.20 m/s (laptop , domain
NAV2+21):

| t (s) | x (m) | z (m) | pitch (rad) | base_vx | wheel qvel |
|---|---|---|---|---|---|
| 0.01 | -0.0005 | -0.0005 | +0.001 | -0.0004 | +0.001 |
| 0.56 | 0.169 | 0.015 | +0.242 | +0.242 | +3.492 |
| 0.98 | 0.284 | 0.038 | +0.710 | +0.416 | +3.470 |
| 1.23 | 0.379 | 0.076 | **+1.206** | +0.391 | +3.466 |
| 1.35 | 0.390 | 0.066 | +1.168 | -0.005 | +3.465 |
| 2.0+ | ~0.399 (frozen) | +0.070 | **+1.20 (locked)** | ~0 (jitter, 60 sign flips) | +3.464 |

The base **pitch ramps monotonically 0 -> +1.21 rad (~69 deg) over the first
~1.2 s, then locks**, x stalls at ~0.40 m, and the wheels keep spinning at the
full commanded 3.464 rad/s. The robot has **tipped forward onto its nose**;
the wheels are off the ground, free-spinning. base_vx mean over the run =
0.096 m/s = 0.48x, but the 0.5x is an ARTIFACT of averaging ~1.2 s of driving
(x 0->0.39 m, up to +0.63 m/s) with ~2.8 s of parked-and-spinning.

**Result 4 — the direct path NEVER tips.** Same startup, same model, same
command: base pitch stays within +/-0.006 rad for the full 4 s, x advances
smoothly to 0.79 m, factor 0.99-1.00x (both startup and settled).

**VERDICT.** The failure is **not** a roller-contact / warm-start / solver
defect. It is a **base pitch instability that only manifests in the live ROS
loop**: a commanded pure  torque tips the (tall, top-heavy: 6.0 kg base
stand-in inertia  + column/arm mass) chassis forward past the
wheel contact line, after which it is parked on its nose with the wheels
spinning freely. The residual ROS-vs-direct difference that PRODUCES the tip
lives in the live control loop (wall-clock-gated stepping with the CM running
its controller / on a **separate realtime_tools RT thread**,
plugin:427-440, so ctrl application is asynchronous to ), not in
, the MJCF, the startup sequence, or the control cadence — all of
which were reproduced faithfully and stay flat at 1.0x.

**Fix direction (revised).** NOT add a settling/homing step before commanding
(refuted: startup replica is flat) and NOT a contact-model retune (refuted:
model byte-identical, contacts form and hold in the replica). The driver is
chassis **pitch stability under velocity command** in the live loop: the base's
stand-in inertia (0.1 0.1 0.1) is ~2 orders below a realistic 6 kg chassis
inertia, so the pitch mode is unrealistically tender; and the tip is triggered
by whatever makes the live ctrl asymmetric vs the direct constant-hold. Two
candidate levers: (a) give the free-jointed base a **realistic inertial**
(from the URDF/base.xacro chassis mass + a sane diaginertia) so it does not tip
under a nominal wheel torque; (b) make the live ctrl application deterministic
w.r.t. stepping. Both are R4-relevant design changes -> **design fork ->
escalate**, not a silent retune. NOTE: this supersedes the earlier
roller-contact warm-start fix direction in this file (that hypothesis is
refuted by Result 1).

Probes (scratch, untracked): 
(startup-vs-settled contact/ncon/actuator trace + sustained factor),
 (arm-pose),  (cadence),
 (live ROS ground-truth base vx/z/pitch trace).

---

## Diagnostic probe (READ-ONLY, 2026-09-29) — warm-start hypothesis REFUTED; failure is a base TIP-OVER

**Method.** The plugin exposes NO contact topic/service (verified from the
plugin source: only GetBodyState / SetBodyPose / StepSimulation /
play-pause-reset), so mjData of the live chain is unreadable. Instead I
replicated the plugin's EXACT startup (mj_resetData -> mj_forward -> ONE mj_step;
plugin.cpp:114-123) in a direct harness and compared per-step contact evolution
against the settled-0.5 s path, both driving the same body_to_wheel(0.20, 0, 0)
= [+3.464, 0, -3.464] rad/s.

**Result 1 — startup sequence is NOT the cause.** Startup-replica vs settled,
sustained window 1.5-4.0 s: startup factor 0.994x / settled 0.957x (both ~1.0x).
Deterministic across reruns. The startup sequence does NOT reproduce the ROS
~0.5x. Also exonerated: arm-actuator pose (arm_gripper_position_controller holds
the reset pose in ROS; replica at ctrl=0 vs held-at-qpos0 -> 0.994 / 0.999 /
1.003 / 1.004x) and control cadence (refresh every 5th step == every step).

**Result 2 — the MJCF is provably identical.** write_mjcf_model text
re-materialized at runtime (~/.ros/sisyphus_derived_scene.xml) is byte-identical
to the direct harness's model (sha match, both 25081 B). The plugin's only model
override is opt.timestep (no-op). So the divergence is NOT
model/solver/startup.

**Result 3 — the live ROS chain TIPS THE BASE OVER.** Ground-truth
GetBodyState('base_link') trace at 0.20 m/s (laptop olivia, domain NAV2+21):

| t (s) | x (m) | z (m) | pitch (rad) | base_vx | wheel qvel |
|---|---|---|---|---|---|
| 0.01 | -0.0005 | -0.0005 | +0.001 | -0.0004 | +0.001 |
| 0.56 | 0.169 | 0.015 | +0.242 | +0.242 | +3.492 |
| 0.98 | 0.284 | 0.038 | +0.710 | +0.416 | +3.470 |
| 1.23 | 0.379 | 0.076 | +1.206 | +0.391 | +3.466 |
| 1.35 | 0.390 | 0.066 | +1.168 | -0.005 | +3.465 |
| 2.0+ | ~0.399 (frozen) | +0.070 | +1.20 (locked) | ~0 (jitter, 60 sign flips) | +3.464 |

The base pitch ramps monotonically 0 -> +1.21 rad (~69 deg) over the first ~1.2 s,
then locks; x stalls at ~0.40 m, and the wheels keep spinning at the full
commanded 3.464 rad/s. The robot has tipped forward onto its nose; the wheels are
off the ground, free-spinning. base_vx mean over the run = 0.096 m/s = 0.48x, but
the "0.5x" is an ARTIFACT of averaging ~1.2 s of driving (x 0->0.39 m, up to
+0.63 m/s) with ~2.8 s of parked-and-spinning.

**Result 4 — the direct path NEVER tips.** Same startup, same model, same
command: base pitch stays within +/-0.006 rad for the full 4 s, x advances
smoothly to 0.79 m, factor 0.99-1.00x (both startup and settled).

**VERDICT.** The failure is not a roller-contact / warm-start / solver defect. It
is a base pitch instability that only manifests in the live ROS loop: a commanded
pure +vx torque tips the (tall, top-heavy: 6.0 kg base stand-in inertia
diag 0.1 0.1 0.1 + column/arm mass) chassis forward past the wheel contact line,
after which it is parked on its nose with the wheels spinning freely. The
residual ROS-vs-direct difference that PRODUCES the tip lives in the live control
loop (wall-clock-gated stepping with the CM running its controller
update()/write() on a separate realtime_tools RT thread, plugin:427-440, so ctrl
application is asynchronous to mj_step), not in mjModel.opt, the MJCF, the
startup sequence, or the control cadence — all of which were reproduced
faithfully and stay flat at 1.0x.

**Fix direction (revised).** NOT "add a settling/homing step before commanding"
(refuted: startup replica is flat) and NOT a contact-model retune (refuted: model
byte-identical, contacts form and hold in the replica). The driver is chassis
pitch stability under velocity command in the live loop: the base's stand-in
inertia (0.1 0.1 0.1) is ~2 orders below a realistic 6 kg chassis inertia, so the
pitch mode is unrealistically tender; and the tip is triggered by whatever makes
the live ctrl asymmetric vs the direct constant-hold. Two candidate levers:
(a) give the free-jointed base a realistic inertial (from the URDF/base.xacro
chassis mass + a sane diaginertia) so it does not tip under a nominal wheel
torque; (b) make the live ctrl application deterministic w.r.t. stepping. Both
are R4-relevant design changes -> design fork -> escalate, not a silent retune.
NOTE: this supersedes the earlier "roller-contact warm-start" fix direction in
this file (that hypothesis is refuted by Result 1).

Probes (scratch, untracked) on olivia /tmp: i141_contact_startup_probe.py
(startup-vs-settled contact/ncon/actuator trace + sustained factor),
i141_arm_probe.py (arm-pose), i141_dec_trace.py (cadence), i141_gt_probe.py
(live ROS ground-truth base vx/z/pitch trace).

---

## i141 verification: base-inertia stress test (2026-09-29, verification worker)

**Question.** Is the live-chain tip-over fixed by stiffening the free-joint
`base_link` diaginertia (=> fix = base inertial), or does it tip regardless
(=> cause = async ctrl application)?

**Composite pitch inertia of the free base subtree (computed, MuJoCo 3.12).**
The subtree is NOT dominated by `_BASE_DIAGINERTIA`. Real terms (parallel-axis
about the base origin, from the derived MJCF + `mj_fullM` on the free joint):

| term | mass kg | r^2 to base | I_pitch contribution |
|---|---|---|---|
| chassis (cyl r0.15 h0.06) | 6.0 | 0.0072 | ~0.04 |
| 3 wheels + 24 rollers | 0.69 | 0.0156 | ~0.01 |
| column rail (fused into base_link) | 2.5 | 0.6084 | ~1.52 |
| column carriage + 2 arms + grippers | 1.286 | ~0.105 | ~0.14 |
| **sum** | **9.37** | | **~1.25** |

MuJoCo computes the free-joint rotational block Iy (pitch) at the base origin as
**1.246** with the 0.1 stand-in. So the stand-in is ~8% of the composite: the
pitch mode is NOT under-inertiad by the diagonal itself

---

## i141 verification: base-inertia stress test (2026-09-29, verification worker)

**Question.** Is the live-chain tip-over fixed by stiffening the free-joint
`base_link` diaginertia (=> fix = base inertial), or does it tip regardless
(=> cause = async ctrl application)?

**Composite pitch inertia of the free base subtree (computed, MuJoCo 3.12).**
The subtree is NOT dominated by `_BASE_DIAGINERTIA`. Real terms (parallel-axis
about the base origin, from the derived MJCF + `mj_fullM` on the free joint):

| term | mass kg | approx r^2 to base | I_pitch contribution |
|---|---|---|---|
| chassis (cyl r0.15 h0.06) | 6.0 | 0.0072 | ~0.04 |
| 3 wheels + 24 rollers | 0.69 | 0.0156 | ~0.01 |
| column rail (fused into base_link) | 2.5 | 0.6084 | ~1.52 |
| column carriage + 2 arms + grippers | 1.286 | ~0.105 | ~0.14 |
| **sum** | **9.37** | | **~1.25** |

MuJoCo computes the free-joint rotational block Iy (pitch) at the base origin as
**1.246** with the 0.1 stand-in. So the stand-in is only ~8% of the composite:
the pitch mode is NOT under-inertia'd by the diagonal itself; it is dominated by
the 2.5 kg rail + arms sitting 0.3-0.8 m above the wheels. (Chassis-only inertia
is ~0.0355; the 0.1 stand-in is ~3x ABOVE the bare chassis, as the task noted.)

**Stress test (live ROS chain, /tmp/i141_gt_probe.py, +vx=0.20, 4 s).**
`_BASE_DIAGINERTIA` swept; sim loaded fresh each run (pycache + derived scene
cleared; derived `~/.ros/sisyphus_derived_scene.xml` grep-verified each time):

| diaginertia | composite Iy | MEAN base_vx frac | final pitch | verdict |
|---|---|---|---|---|
| 0.1 (stand-in) | 1.246 | 0.50x | +1.211 rad | TIPS |
| 0.20 | 1.346 | 0.50x | +1.211 rad | TIPS |
| 0.28 | 1.426 | 0.52x | +1.211 rad | TIPS |
| 0.50 | 1.646 | 0.68x | (locked) | TIPS |
| 0.70 | 1.846 | 0.61x | +1.158 rad | TIPS |
| **0.71** | **1.856** | **0.97x** | **~0.000 rad** | **no tip** |
| 0.72 | 1.866 | 1.01x | ~0.000 | no tip |
| 0.80 | 1.946 | 1.01x | ~0.000 | no tip |
| 1.00 | 2.146 | 0.86-1.01x | ~0.000 | no tip |

**(1)** Stiffening the base inertia DOES stop the tip: `diaginertia 1.0 1.0 1.0`
=> final pitch ~0 rad, x delivers ~0.73 m (0.86-1.01x). vs the 0.1 stand-in
=> pitch +1.211 rad (~69 deg), x stalls ~0.41 m (0.50x). **(2)** The minimum
diaginertia that prevents tipping is ~**0.71** (0.70 tips, 0.71 does not;
reproduced 2/2 each). That is a composite Iy of ~1.86 vs ~1.25 at the stand-in.

**Verdict (revised, with the caveat).** The tip IS inertia-gated in the live
chain -- it is not an inertially-insensitive async-control-only artifact, since a
stiff base fully cures it while leaving ctrl application unchanged. BUT the
required stiffness (~0.71, composite Iy ~1.86) is ~20x the bare-chassis 0.0355
and ~1.5x even the full composite 1.246, i.e. NOT a "realistic chassis inertia" --
a realistic base inertial (~0.04-0.07, or the 0.1 stand-in) does NOT prevent the
tip. So the fix is NOT simply "make the base inertial realistic"; the realistic
value sits well below the tip threshold. The tip is a real top-heavy pitch
instability of the plant (rail + arms ~0.3-0.8 m up on a 0.125 m wheelbase) that
the live loop's asymmetric/asynchronous ctrl application drives hard enough that
the plant's own pitch inertia must be ~1.5x the physical composite to survive.
Reducing rider height/mass, widening the footprint, or making ctrl application
deterministic would each address it at the source; a stiff stand-in inertial only
masks it.

**NOT changed.** Source restored to committed state:
`_BASE_DIAGINERTIA = '0.1 0.1 0.1'` (`src/robot_description/robot_description/mjcf_model.py:96`).
No source change kept; the sweep was reversible via sed.
CAVEAT: `iyy`-only tests (e.g. `0.1 1.0 0.1`) are IMPOSSIBLE -- MuJoCo rejects
them ("inertia must satisfy A + B >= C"); only isotropic (or triangle-legal)
tensors load. So the pitch-axis-specific causal test could not be run directly.

---

## i141 verification: velocity-STEP-transient hypothesis — tested (2026-09-29, verification worker)

**Question.** Is the live-chain tip-over a *velocity-step transient* — i.e. the
cmd_vel→wheel bridge stepping the wheels 0 → ±3.464 rad/s instantly, whose
initial wheel-torque reaction pitches the base? If so an **acceleration limit
(ramped cmd_vel)** — a physical parameter, NOT a vx/vy cap — should prevent it.

**Method.** Probes are /tmp scripts only (no repo source touched); `_ROLLER_MASS`
0.01, `_BASE_DIAGINERTIA` `0.1 0.1 0.1` UNCHANGED. Torque is the wheel actuator
torque `qfrc_actuator` = the `effort` field of `/joint_states` in the live chain
(broadcaster publishes position+velocity+effort; `mujoco_system.cpp:405`) and
`d.actuator_force` in the direct harness (same quantity). Rate is the *live*
cmd_vel→wheel chain via `omni_base_controller` (bridge src).

**1) Torque transient, STEP +vx=0.20 (0→0.20 instantly):**

| path | peak abs wheel actuator torque | 90% rise | final pitch | x | tip? |
|---|---|---|---|---|---|
| DIRECT (no ROS) | **34.63 N·m** @ t=0.002 s | 0.002 s | +0.0024 rad | 0.805 m | no |
| LIVE ROS chain | **0.79–0.95 N·m** @ t=0.46–0.67 s | 0.46 s | **+1.211 rad** | 0.40 m | **YES** |

**The direct path applies a 44x LARGER, 200x SHARPER torque step and does NOT
tip.** Its torque is a 1–3-step impulse (34.6 N·m at t=0.002 s — the velocity
actuator's P-term `gain*error` = 10 x 3.464 — collapsing to ~0 by t=0.008 s).
The live chain's wheel torque over the first 0.14 s is *tiny* (<= 0.03 N·m) and
only becomes significant (0.69 N·m) at t~0.29 s, AFTER the base is already
pitching (+0.02 -> +0.28 -> +0.87 rad). So the live tip is **NOT** an initial
torque-spike reaction; the torque *grows* as the base pitches and the wheel
geometry degrades. The initial transient is exonerated as the magnitude driver.

**2) Ramp test, LIVE only, vx(t)=0.20*min(1,t/R) (base inertia + roller mass unchanged):**

| R (s) | peak torque | final pitch | delivered x | tip? | reps |
|---|---|---|---|---|---|
| 0 (step) | 0.79–0.95 | **+1.211** | 0.40 m (0.50x) | **YES** | 3/3 tip |
| 0.005 | 0.90 | **+1.211** | 0.40 m | **YES** | 1/1 tip |
| 0.01 | ~0.9 | **+1.211** | 0.40 m | **YES** | 3/3 tip |
| 0.015 | 0.61 | -0.002 | 0.80 m | no | 1/1 |
| 0.02 | 1.31 | +1.211 / -0.002 | 0.40 / 0.80 | 2 tip / 1 no | 3 (borderline) |
| 0.03 | ~0.7 | -0.0015 | 0.80 m (1.00x) | **no** | 3/3 |
| 0.05 | 0.53 | -0.0011 | 0.80 m (1.00x) | **no** | 4/4 |
| 0.10 | 2.88 | +0.0010 | 0.80 m | no | 1/1 |
| 0.20 | 2.51 | +0.0013 | 0.82 m | no | 1/1 |
| 0.50 | 1.46 | -0.0010 | 0.83 m | no | 1/1 |
| 1.0 | 0.83 | +0.0007 | 0.78 m | no | 1/1 |
| 2.0 | 0.45 | +0.0016 | 0.68 m | no | 1/1 |

**A gentle ramp DOES prevent the tip** with base inertia 0.1 and roller 0.01
unchanged, and the delivered x *recovers to ~1.0x* (0.80 m at R>=0.03) vs the
step's 0.50x — the robot walks instead of nosing over.

**3) Minimum ramp time that prevents tipping.** Deterministic tip at R <= 0.01 s;
**borderline/stochastic at R = 0.02 s** (2/3 tip); reliably no-tip at **R >= 0.03 s**
(3/3), and solidly safe at **R = 0.05 s** (4/4). Take the threshold as
**R_min ~= 0.03–0.05 s**, i.e. a max body acceleration of
**0.20/0.05 = 4.0 m/s^2** (conservative) up to `0.20/0.03 = 6.7 m/s^2` — the
command reaches 0.20 m/s in 30-50 ms instead of instantaneously.

**VERDICT — acceleration limiting at the cmd_vel→wheel bridge is a real fix.**
The live tip is NOT a torque-magnitude transient (the direct path has a 44x
larger, sharper torque step and stays flat); it is triggered by the *sharpness
of the commanded-velocity step through the live loop*, and a trapezoidal cmd_vel
ramp of **R >= ~0.03–0.05 s (max accel <= ~4–6.7 m/s^2)** prevents it while
restoring ~1.0x delivery. This is a legitimate physical parameter (an
acceleration/jerk limit on the base command), **distinct from the forbidden
vx/vy magnitude cap** — the steady-state speed is unchanged at 0.20 m/s. The
repo's `omni_base_controller` currently exposes NO acceleration/ramp parameter
(only `publish_rate`, `cmd_vel_timeout`, topics), so the fix would add an
acceleration limit there (or in Nav2's velocity smoother, the existing
accel-limit surface in nav2).

**NOT changed.** Repo source untouched: `_ROLLER_MASS = 0.01`,
`_BASE_DIAGINERTIA = '0.1 0.1 0.1'` (verified at `mjcf_model.py:155` / `:96`).
`git diff --name-only` lists only this `status.md`. Probes (scratch, /tmp on
olivia): `i141_torque_ramp_probe.py` (both paths; torque+pitch transient and the
live ramp sweep), `i141_js_names.py`/`i141_js_names2.py` (confirm /joint_states
carries `effort`), `run_i141.sh` (sources install + CONDA_PREFIX under pixi run).

---

## Manager rulings (FINAL, 2026-09-29) — the verified fix supersedes all prior "design fork" escalations

The tip-over root cause is CONFIRMED (base pitch tip-over, NOT contact slip) and
the fix is VERIFIED (acceleration limit at the cmd_vel->wheel seam). These
rulings REPLACE the earlier R1-R4 in this file and the "design fork / escalate"
state; the loop now proceeds to implementation.

- **R1 — keep the workaround dropped, permanently.** `_ROLLER_MASS = 0.01`
  (`mjcf_model.py:155`) and `_BASE_DIAGINERTIA = '0.1 0.1 0.1'`
  (`mjcf_model.py:96`) stay as-is. Do NOT reintroduce 0.16; do NOT stiffen the
  base inertia. (The base-inertia stress test showed the no-tip threshold is
  ~0.71 diaginertia -- ~1.5x the FULL physical composite -- so "realistic
  inertia" is NOT the fix.)

- **R2 — the fix is an acceleration limit in `omni_base_controller`.**
  Add a configurable `max_accel` (linear, default **4.0 m/s^2**) to
  `src/robot_nav/robot_nav/omni_base_controller.py`, which consumes `/cmd_vel`
  directly (what the fidelity test drives). A trapezoidal cmd_vel ramp of
  R >= 0.03-0.05 s (max body accel <= ~4-6.7 m/s^2) prevents the tip and
  restores ~1.0x delivery. This is a legitimate physical parameter (real robots
  cannot teleport to speed), DISTINCT from the forbidden vx/vy magnitude cap:
  steady-state speed is unchanged. Keep `max_angular_accel`/jerk simple (do not
  over-engineer). Do NOT put the fix only in Nav2's velocity smoother -- the
  fidelity test bypasses it.

- **R3 — the fidelity regression must pass WITH a STEP cmd_vel.**
  `test_base_delivers_commanded_vx_through_the_ros_chain` (in
  `src/robot_bringup/test/test_pr2_navigate.py`;
  `VX_FIDELITY_COMMANDS=(0.10,0.14,0.20)`, `MIN_VX_FIDELITY_RATIO=0.85`,
  `MAX_VX_FIDELITY_DYAWR=0.30`) MUST pass with a STEP cmd_vel: the accel limit
  lives in the controller, so the step is ramped internally and the base must
  NOT tip. Verify no-tip (explicit pitch bound) + >= 0.85x at all three speeds.

- **R4 — closed-loop `NavigateToPose` must still converge (do NOT regress
  #134/#131).** `test_navigate_to_pose_converges_within_tolerance` (goal
  (0.60, -0.45, yaw -1.0), error_xy < 0.10, succeeded=True) must still pass.
  If the accel limit breaks closed-loop convergence, escalate IN-PROCESS (do not
  silently drop the check).

**Red-before-green contract for the implementer:** the new/extended regression
test must FAIL without the accel limit (a 0.20 m/s STEP tips the base -> ~0.5x
delivery) and PASS with it (~1.0x, no tip).
