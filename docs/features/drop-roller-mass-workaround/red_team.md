# red_team.md — i141: drop the #138 roller-mass workaround (VERIFIED findings)

## Verdict
**BLOCK.** The issue's "plain value edit" acceptance branch **fails**: `_ROLLER_MASS =
0.01` kg does **not** deliver >= 0.85x at all three speeds through the 3.12 ROS
chain. 0.20 m/s delivers only **0.666x** (a ~0.13 m/s velocity ceiling). The
workaround cannot be dropped as a plain value edit.

## Empirical results (all run on the laptop, worktree @ branch `feat/i141-drop-roller-mass-workaround`)

### 1. The acceptance regression, 0.01 kg, through the shipped ROS chain
`test_base_delivers_commanded_vx_through_the_ros_chain` (MuJoCo 3.12.0, `mujoco.launch.py`):

| command vx | delivered | dyaw | verdict |
|---|---|---|---|
| 0.10 m/s | **1.037x** | -0.003 | PASS |
| 0.14 m/s | **0.934x** | -0.167 | PASS |
| 0.20 m/s | **0.666x** | +0.113 | **FAIL** (dx=0.400 m) |

Delivered *absolute* speeds are ~0.104 / 0.131 / 0.133 m/s — a **soft ceiling at
~0.13 m/s**, not a #137-style hard stall (the base keeps moving, just under-rate).

### 2. Same 0.01 kg roller, DIRECT 3.12 path (no ROS chain) — holds
Probed `write_mjcf_model` + `MjModel.from_xml_path` + the `_drive` protocol
(same IK, same `ctrl`=wheel-rate, same timestep 0.002 s, same `velocity kv=10`
actuator):

| command vx | delivered |
|---|---|
| 0.10 | 0.999x |
| 0.14 | 1.005x |
| 0.20 | 1.002x |
| 0.30 | 0.995x |

So the light roller is **fine on the direct 3.12 path** — the slip is specific to
the ROS chain.

### 3. 0.16 kg roller through the ROS chain — holds (confirms #138)
Re-ran the same acceptance regression with `_ROLLER_MASS = 0.16`:

| command vx | delivered | dyaw |
|---|---|---|
| 0.10 | 1.000x | -0.001 |
| 0.14 | 0.995x | -0.014 |
| 0.20 | 0.998x | -0.003 |

0.20 m/s = 3.46 rad/s wheel rate, delivered ~1.0x — so the mass is the effective
lever, and the ROS chain can deliver 0.20 m/s *when the roller is heavy*.

### 4. Ruled out (all VERIFIED, not reasoned)
- **Solver generation**: both paths link `libmujoco.so.3.12.0` (objdump on the
  plugin). The 3.9/3.12 split is gone; the slip persists on 3.12.
- **Timestep**: direct = 0.002 s (MJCF default); ROS chain = 1/500 = 0.002 s. Same.
- **Integrator**: both `<option integrator="implicit"/>` (same MJCF).
- **Solver params**: both MuJoCo defaults (Newton, iterations=100, tol=1e-8,
  pyramidal cone, impratio=1.0). The dfki-ric plugin only overrides `opt.timestep`
  and `opt.geomgroup` (grep over all plugin source).
- **Actuator**: both `<velocity kv="10">`; the plugin classifies it VELOCITY and
  writes `ctrl` = commanded velocity — identical to the direct `_drive`.
- **IK / signs**: `omni_base_controller` uses the same matrix/signs as the direct
  probe.
- **velocity_limit clamp**: 0.16 kg delivering 3.46 rad/s proves the clamp does
  not bind (it would cap ~2 rad/s -> 0.115 m/s for *any* mass).
- **arm/gripper position controllers**: reproducing them in the direct path
  (hold all 13 position actuators at home) leaves delivery at ~1.0x — they are
  not the trigger.
- **impratio=10**: made it *worse* (0.10 -> 0.781x, 0.14 -> 0.795x, 0.20 ->
  0.688x) — constraint-force mixing is not the lever.

## Conclusion / what this reframes
The #138 premise "0.01 kg slips only under the vendored **3.9** solver; 3.12
holds it" is **incomplete**. On 3.12 the direct path holds 0.01 kg, but the
**ROS chain still slips** (0.666x at 0.20 m/s). 3.12 changed the symptom (hard
stall -> steady partial slip) but did not resolve the underlying light-roller
contact instability *through the ROS chain*. The 0.16 kg was doing more than
papering over a version delta.

The trigger is a genuine, as-yet-unpinned interaction between the light roller
(0.01 kg) and the ROS chain's runtime that the clean direct path does not
reproduce. Candidate next probes (not yet run): instrument the ROS chain to
read the **wheel joint velocity** during the drive to split "contact slip" (wheel
at 3.46 rad/s, base at 0.133 m/s) from "command/actuation drop" (wheel under-rate);
compare per-step `mjData` (contact count, roller tangent slip, actuator force)
between direct and ROS chain at the same command.

This is a **design fork** — the fix direction (contact-model change vs solver
tuning vs actuation) is not yet determined and should not be guessed.
