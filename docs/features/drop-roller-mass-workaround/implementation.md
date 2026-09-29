# implementation.md — i141: drop the #138 roller-mass workaround

## Change
Plain value edit (not a `git revert`) in
`src/robot_description/robot_description/mjcf_model.py`:

- `_ROLLER_MASS`: **0.16 -> 0.01 kg** — restores the physical value (a real
  omniwheel barrel roller is grams). The 0.16 kg from #138 was a workaround for
  the then-vendored MuJoCo **3.9.0** solver, not a physical quantity.
- The stale comment block above it (which documented the "#137 raised
  0.01 -> 0.16 for the vendored 3.9 solver" rationale) was replaced with a
  concise, factual note: 0.01 kg is the physical value; the #138 0.16 kg
  workaround is dropped now that the ROS sim links **MuJoCo 3.12.0** (D38,
  #139/#140 merged, issue #141); R9 diagnosed the original stall as a
  3.9-vs-3.12 solver-generation delta.

No other source, test, or config file was changed. No mitigation/caps. No
version change (3.9 is not reintroduced).

## Why the inertia constants were NOT edited
`_ROLLER_I_AXIS` and `_ROLLER_I_TRANS` are **computed from `_ROLLER_MASS`**
at import time:

```python
_ROLLER_I_AXIS  = _ROLLER_MASS * _ROLLER_RADIUS ** 2 / 2.0
_ROLLER_I_TRANS = _ROLLER_MASS * (3.0 * _ROLLER_RADIUS ** 2
                                  + (2.0 * _ROLLER_HALF_LENGTH) ** 2) / 12.0
```

so the mass edit propagates to the inertia tensor automatically — editing them
by hand would risk a stale tensor (and would be redundant). Per ruling R1.

## Acceptance gate (NOT run here)
The acceptance gate is the ROS-chain fidelity regression
`src/robot_bringup/test/test_pr2_navigate.py::test_base_delivers_commanded_vx_through_the_ros_chain`,
parametrized at **vx = 0.10 / 0.14 / 0.20 m/s**, asserting:
- delivered/commanded ratio **>= 0.85x**, and
- **|dyaw| <= 0.30** rad,

measured via ground-truth `GetBodyState('base_link')` displacement over 3 s
through the shipped `mujoco.launch.py` ROS chain, against **MuJoCo 3.12.0**.

That test is run by the **test-runner / red-team** step of the loop, not by the
implementer; this doc only records the change. If 0.01 kg slips on 3.12 through
the ROS chain, that is a genuine design fork -> escalate (do NOT keep 0.16, per
R4).

## Commits
- `fca738e` — `fix(i141): drop #138 roller-mass workaround — _ROLLER_MASS 0.16 -> 0.01 on MuJoCo 3.12` (the `mjcf_model.py` value + comment edit).
- (this commit) — `docs(i141): implementation notes for dropping the roller-mass workaround` (this file).

Build: `pixi run build` green — Summary: 16 packages finished.
