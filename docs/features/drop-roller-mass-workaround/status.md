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
None yet — pending the empirical fidelity-regression result on the 3.12 sim.

## Loop state
- [x] Worktree created @ c0568e4 (latest origin/main).
- [x] Provisioned: `pixi install` + `pixi run install-openclaw` (OpenClaw 2026.9.6).
- [ ] Build (with `src/mujoco` + `src/mujoco_ros2_control` @ pinned commits).
- [ ] Implement (value + comment).
- [ ] Fidelity regression (0.10/0.14/0.20 through ROS chain) — the acceptance gate.
- [ ] Red-team (read-only, N+1).
- [ ] Test-runner full `pixi run test`.
- [ ] PR (squash-merge), report "ready".
