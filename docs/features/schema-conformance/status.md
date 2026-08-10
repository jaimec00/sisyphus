# Status — schema-conformance (issue #33)

**Feature:** Conform skill/observation schema to D17–D19 — `SCHEMA_VERSION` +
golden-fixture guard, `grasped` flag, FailureCode limit-split.
**Branch:** `feat/i33-conform-skill-observation-schema-to-d17`
**Base:** `origin/main` @ `0a66386`

| Phase | State |
|---|---|
| 0. Sync | done — worktree == origin/main @ 0a66386 |
| 1. Brief (issue #33) | done — body present, acceptance criteria clear |
| 2. Context | done — `context.md` |
| 3. Implement | done — 5 commits, `pixi run test` 236 tests / 0 failures |
| 4. Red-team (round 1) | done — 2 BLOCK, 6 NOTE (`red_team.md`) |
| 5. Fix (round 1) | done — 4 commits, `pixi run test` 252 tests / 0 failures |
| 5b. Red-team (round 2) | done — both BLOCKs closed, no new BLOCK (`red_team_round2.md`) |
| 6. Test-runner | done — build 7/7; 252 tests, 0 failures, 0 skipped, AUDIT PASSED |
| 7. PR + ready | **done — PR #34, ready for Sisyphus** |

**PR:** https://github.com/jaimec00/sisyphus/pull/34 (rebased on `origin/main`
@ `0a66386`; main had not moved, so green is green against current main)
**Follow-ups** → comment on issue #33 (3 items; Sisyphus files them)
**Retro** → comment on PR #34
**Note for merge:** the `docs/features/` CI check reads as failing by design —
these docs stay for review; Sisyphus deletes them at merge.

**Round:** —
**Blockers:** none
**Escalations:** none

## Manager rulings (open questions raised by `context.md`)

The context pass surfaced three questions the brief does not resolve. Decided
here by the worktree manager (all within scope; none is a design fork worth
escalating):

1. **`schema_version` at two nesting depths in `SkillResult.to_dict()`** —
   stamp **both**. The stamp belongs to the *type's* wire form, not to a
   message envelope: an `Observation` published on its own is self-describing,
   and suppressing its stamp when nested would require special-casing the
   nested call. Redundancy is cheap and keeps `Observation.to_dict()` a single
   canonical function.
2. **`GRIPPER_EMPTY` bucket (absent from both of the brief's lists)** —
   **backend refusal**. Placing with nothing held is a precondition failure
   ("can't be done"), not an in-flight abort ("unsafe to continue"). The
   classifier must additionally be *exhaustive*: a test asserts every
   `FailureCode` member lands in exactly one bucket, so a future code cannot be
   silently unclassified.
3. **What `grasped` tracks vs. `held_object_id`** — an **independent field**,
   not an alias of `is_holding`. `held_object_id` is a world-model fact (*which*
   object); `grasped` is a sensed fact (*something* is held). On a real backend
   the two diverge — force/aperture sensing can report `grasped=True` for an
   unidentified object. The Mock has no force sensing, so it derives
   `grasped` from whether it holds something, but the field stays separate on
   the wire.
