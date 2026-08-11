# Red-team round 2 — the B1/N3/N4/N5 fix commits (issue #43)

Read-only review of the **delta only** (`32c64bf`, `85ddd1f` — the new
`policy.py`, the new `UNCLASSIFIED_SKILL` event kind, and the N3/N4/N5
hardenings). Round 1's findings and its cleared hypotheses are not
re-litigated; `red_team.md` stands unchanged.

**Verdict: 0 BLOCK, 3 NOTES.** The fixes do what they claim, they preserve the
behaviour of the three rewritten dispatch sites exactly, and the new tests are
load-bearing rather than table-restating. Every one of the eight attacks the
manager named was run; six hold outright, two produced NOTES, and one produced
a NOTE I considered promoting and deliberately did not (N1 below, with the
reasoning stated so the manager can overrule me cheaply).

---

## BLOCK

*(none)*

---

## NOTES

### N1 — the flag→field promise is checked in one direction only, so B1's hole can be re-opened by a *wrong* classification instead of a missing one

`src/robot_safety/test/test_skill_policy.py:54-73`
(`test_each_flag_implies_the_field_the_layer_will_reach_for`),
`src/robot_safety/robot_safety/policy.py:78-99`.

The tripwire forces a **decision** for every registered skill
(`set(SKILL_POLICIES) == set(SKILL_TYPES)`, `test_skill_policy.py:37`) — but
nothing forces that decision to be consistent with the skill's *shape*. The
coupling test only asserts *flag ⇒ field exists*. The converse — *field exists
⇒ flag set* — is unchecked for the two fields where it is mechanically
derivable.

**Concrete failure scenario.** A later feature adds
`WipeSurface(pose: Pose, passes: int)` to `robot_skills`. The developer runs
this suite, `test_every_registered_skill_has_a_policy` fails as designed, and
they add a row. Reading the table they see rows whose flags are about *grasping*
and *lifting*, conclude "a wipe is neither", and write:

```python
WipeSurface.name: SkillPolicy(),   # nothing applies
```

Result: `target_pose()` returns `None` for every wipe
(`collision.py:45-48`), so `KeepOutBoxGuard` waves through a wipe target
inside `stove_top` or below the floor — and **all 169 tests stay green**,
including the tripwire, which is satisfied by the row's mere existence. That is
the same fail-open B1 closed, reached one step further along: by a plausible
mis-classification rather than by omission.

**Fix direction (two lines, in the test that already imports `fields`).**

```python
if 'pose' in names:
    assert policy.has_cartesian_target, name
if 'height' in names:
    assert policy.clamps_column_height, name
```

`side` deliberately gets no converse rule — `OpenGripper` and `Place` both
carry a `side` and must *not* be force-gated (R11), and that exemption is
already pinned by `test_skill_policy.py:87-92`. Worth saying so in a comment,
because the asymmetry is the reason the converse looks unwritable at a glance.

**Why NOTE and not BLOCK.** Round 1's own promotion criterion for B1 was "a
fail-open triggered by ordinary forward development *rather than by a mistake*".
This one requires a mistake: the developer is stopped, made to look at the
table, and shown six worked rows each carrying a `why` comment. Shipped
behaviour today is correct — all seven rows are right, and the
`has_cartesian_target` set is fully pinned behaviourally (see test adequacy
below). I record it as the round's top finding and would not argue with a
manager who promotes it, since the fix is smaller than this paragraph.

### N2 — policy is selected by a value the object *carries*, so a shape mismatch escapes `filter` as an `AttributeError`

`src/robot_safety/robot_safety/policy.py:110` (`SKILL_POLICIES.get(skill.name)`),
`src/robot_safety/robot_safety/layer.py:109` (`skill.side`),
`layer.py:317` (`skill.height`), `collision.py:48` (`skill.pose`).

Answering the manager's question directly: the lookup is by `skill.name`, the
`ClassVar` — **not** by `type(skill)`. Three sub-attacks:

* **Spoofing by a caller: does not hold.** `name` is a `ClassVar`, not a
  dataclass field, so no constructor and no `from_dict` path sets it per
  instance (`skills.py:94`, `:129-145` dispatches *through* the registry).
  There is no untrusted input that steers policy selection.
* **Subclass sharing a parent's wire name: benign, and unchanged from round 0.**
  A `register=True` subclass *cannot* share a name (`skills.py:107-110` raises).
  `class FastGrasp(Grasp, register=False)` inherits `name = 'grasp'` and gets
  `Grasp`'s policy — exactly what the `isinstance(skill, Grasp)` chain it
  replaced did. Not a regression. (One pre-existing wrinkle it inherits: a
  clamped `ExtendColumn` subclass is rebuilt as a plain `ExtendColumn`,
  `layer.py:331`, dropping any extra fields. Round 1 cleared that site; noting
  it only because name-keying makes the subclass case slightly more thinkable.)
* **A name-squatter of the wrong shape: new, and it raises.** This is the
  runtime instantiation of the manager's attack 4. The flag→field promise is
  validated only against `SKILL_TYPES[name]`, but the lookup accepts *any*
  object carrying that name:

  ```python
  @dataclass(frozen=True)
  class Odd(Skill, register=False):
      name: ClassVar[str] = 'grasp'      # no `side` field
  layer.filter(Odd(), make_state())      # -> AttributeError: 'Odd' object has no attribute 'side'
  ```

  `_closing_sides` reaches for `skill.side` on the strength of the *name*, and
  an `AttributeError` escapes a function whose published contract is "a
  structured event, never an exception" (`events.py:13`, `README.md:4-5`).

**Why NOTE.** It fails **closed** (nothing executes), it needs a deliberately
perverse class, and no code in the workspace can produce one. **Fix direction**
— two lines in `policy_for`, which also makes the flag→field promise true by
construction rather than by test:

```python
registered = SKILL_TYPES.get(skill.name)
if registered is None or not isinstance(skill, registered):
    return None          # -> UNCLASSIFIED_SKILL, a structured refusal
```

Genuine subclasses still classify; impostors become unclassified and get the
fail-closed *event* the layer promises instead of a traceback.

Smaller sibling, same file: `_clamp` re-checks `policy is None`
(`layer.py:314`) while `_closing_sides` does not (`layer.py:107`). The
asymmetry is harmless today — `_check_classified` guarantees non-`None` by then
(and says so, `layer.py:285-286`) — but the two sites disagree about whether
that guarantee is worth restating.

### N3 — the raise-vs-return boundary is now coherent, but it is documented nowhere a caller or a guard author reads

`src/robot_safety/robot_safety/layer.py:168-178` (`filter` docstring),
`layer.py:242-251` (the two new raises), `collision.py:52-56` (the protocol),
`src/robot_safety/README.md:3-5`.

Judging the boundary rather than defending it, as instructed. The package **is**
self-consistent after N3's fix, on a rule that can be stated in one line:
*exceptions are programmer/infrastructure errors; events are safety verdicts.*
Caller passes the wrong type → `TypeError`. Injected guard violates its
protocol (returns junk, or returns a clamp record) → `TypeError`/`ValueError`.
Guard raises internally → propagates (round 1's N6, accepted). Robot state is
unsafe → `SafetyEvent`. No safety verdict has become an exception and no
programmer error has become a verdict; N3's new `ValueError` sits on the same
side of the line as the `TypeError` that was already there, so it is not "a
third-party guard bug crashing `filter` in a new way" — it is the same category
as the crash we already accepted, and both fail closed.

What is missing is that the rule is never written down where it binds:

* `filter`'s docstring documents exactly one raise — "`TypeError` for arguments
  of the wrong type" — and is now silent about the `TypeError`/`ValueError` a
  guard can trigger and about guard exceptions passing through.
* `README.md:5` says the layer returns "a structured safety event rather than
  raising", full stop.
* `CollisionGuard` (`collision.py:52-56`), the docstring a *guard author*
  actually reads, states neither obligation the layer enforces: `check` must
  never return a clamp record, and `check` must be total. Both rules live at the
  enforcement site in `layer.py`, i.e. in the file the implementor does not open.

Fix direction: one `Raises:` sentence on `filter`, and move the two guard
obligations onto the `CollisionGuard` protocol docstring (the enforcement can
stay where it is). This also gives N6, when someone files it, a stated
status-quo to change rather than an undocumented behaviour to discover.

---

## Attacks that hold (stated explicitly, per instruction)

**A2 — `UNCLASSIFIED_SKILL`'s position and reachability: holds.** The order in
`filter` (`layer.py:188-194`) is e-stop → classified → collision → velocity →
force → clamp, matching R10 with the new check inserted second, and both halves
are tested: an unclassified skill under e-stop reports `ESTOP_ENGAGED`
(`test_safety_layer.py:397-401`), and the refusal happens *before any guard is
asked* — proved with a recording guard that must see zero calls, then one
(`test_safety_layer.py:404-421`). Second is the right slot: e-stop is the more
urgent true statement, and asking a geometry checker about a command nobody has
classified would be asking a question whose answer cannot be acted on.
Genuinely reachable at runtime, not just constructible: `SKILL_TYPES` is a live
`MappingProxyType` over the registry dict (`skills.py:331`), `policy_for`
consults the table on every call, so any skill class defined anywhere in the
process — downstream package, plugin, `register=False` intermediate base
(whose inherited `name` is `''` → refused) — lands on the refusal path without
`robot_safety` being rebuilt.

**A3 — the three rewritten dispatch sites preserve behaviour exactly: holds.**
Site by site against round 1's transcription of the `isinstance` chains:

| site | old predicate | new flag set | verdict |
|---|---|---|---|
| force check (`layer.py:107`) | `(CloseGripper, Grasp)` | `closes_jaws` = `{close_gripper, grasp}` | identical |
| column clamp (`layer.py:314`) | `ExtendColumn` | `clamps_column_height` = `{extend_column}` | identical |
| `target_pose` (`collision.py:46`) | `(MoveGripper, Place)` | `has_cartesian_target` = `{move_gripper, place}` | identical |

No inversion, no widening, no skill changing category. `Place` carries a `side`
but is correctly **not** `closes_jaws` (it opens), and `OpenGripper` remains
un-gated by over-force (R11) — pinned twice, as a table row
(`test_skill_policy.py:87-92`) and as behaviour at 900 N
(`test_safety_layer.py:185-204`). `Grasp(side=None)` still checks **both**
sides: `_closing_sides` returns `SIDE_ORDER` (`layer.py:110`), tested with a
left-only over-force reading (`test_safety_layer.py:147`).

**A1 (third bullet) — the registry comparison is bidirectional: holds.**
`unclassified_skills` itself is one-directional by design
(`policy.py:120`, `set(registry) - set(SKILL_POLICIES)`), but the test pairs it
with `set(SKILL_POLICIES) == set(SKILL_TYPES)` (`test_skill_policy.py:37`), so a
policy row naming nothing in the registry fails too. A *rename* cannot strand a
row at all, because the keys are `NavigateTo.name`, `Grasp.name`… — derived
from the classes rather than typed as literals (`policy.py:82-98`), so they
follow a rename, and a deletion breaks the import loudly. This is a better
construction than the fix direction round 1 proposed.

**A6 — N4/N5 caused no regression: holds, and both are properly wired.**
N4: cap positivity moved to `MotionLimits.__post_init__` via `_as_positive`
(`limits.py:93-110`, `:179`, `:188`). Every construction path in the workspace
survives — the parse path (`limits.py:203-209`) is unchanged in effect, the
shipped `limits.yaml` caps are all positive, and the only direct
`MotionLimits(...)` construction outside the parser is the new negative-case
test itself (`test_limits_config.py:254`; grepped workspace-wide). Exception
type widened from `ValueError`/`TypeError` to `SafetyConfigError` on the direct
path, which is a `ValueError` subclass, so no `pytest.raises` regresses.
N5: `from_limits` raises on a boxless set (`collision.py:104-107`), and the
documented default path is untouched — `SafetyLayer()` defaults to
`NullCollisionGuard` (`layer.py:146-147`, asserted at
`test_collision_guard.py:53-58`), the shipped `limits.yaml:56-61` does define
`below_floor`, and both directions are tested in one place:
`from_limits(boxless)` raises, `KeepOutBoxGuard(())` still constructs, and
`from_limits(SafetyLimits.defaults()).boxes` is non-empty
(`test_collision_guard.py:210-223`). The optional YAML section did not become a
trap: omitting it is still legal and still tested
(`test_limits_config.py:201-206`).

**A8 — read-only discipline: holds.** No Bash in this session, so I established
it by file mtime ordering rather than `git diff`: every file modified after
`scripts/tests/test_ratchet.py` (the already-approved earlier commit) lies in
`src/robot_safety/**` or `docs/features/safety-clamp-layer/**`. Nothing under
`src/robot_skills/**`, `scripts/`, `docs/design/`, `.github/` or any sibling
package post-dates it. `scripts/test_baseline.json` is untouched.

---

## Test adequacy — explicit assessment of the ~31 new tests

**Adequate.** They are behavioural, not table-restating, with two deliberate
exceptions I name below. Answering the manager's question — *which new test
would still pass if the corresponding check were deleted?* — I traced each
delta test against deletion of the code it guards:

| delete this | this fails |
|---|---|
| `_check_classified` from the order (`layer.py:191`) | `test_a_skill_this_layer_cannot_classify_is_refused` (`test_safety_layer.py:372`), `…_even_when_everything_reads_nominal` (`:386`), `…_before_any_guard_is_asked` (`:404`) |
| the `event.is_clamp` raise (`layer.py:246-250`) | `test_a_guard_that_returns_a_clamp_record_is_refused` (`test_collision_guard.py:177`) |
| `ClampedCall`'s abort-event rejection (`layer.py:85-88`) | `test_a_clamped_call_refuses_an_abort_event_as_a_clamp_record` (`test_safety_layer.py:438`) |
| `_as_positive` from `MotionLimits.__post_init__` | `test_a_cap_built_in_python_is_held_to_the_same_rule_as_one_from_yaml` (`test_limits_config.py:241`) — 4 of its 5 params |
| the boxless check in `from_limits` | `test_building_a_box_guard_from_a_boxless_config_is_refused` (`test_collision_guard.py:210`) |
| any policy row / flag | at least one of `test_skill_policy.py:37`, `:68-73`, `:84`, `:91`, plus the behavioural clamp/force/geometry tests |

Two new tests do restate the table rather than exercise behaviour —
`test_the_clamped_scalar_is_the_column_height_and_only_that`
(`test_skill_policy.py:76`) and `test_opening_the_jaws_is_deliberately_unchecked`
(`:87`). Both would pass if `_clamp` or `_check_gripper_force` ignored their
flag entirely. They earn their place anyway as *design pins* (each names a
decision — R3, R11 — that must not change silently) and neither is the only
cover: the behaviour is independently held by `test_safety_layer.py:96-114` and
`:185-204`. Flagging them so the manager does not count them twice.

Three specific quality observations:

* `test_the_stand_in_skill_does_not_pollute_the_shared_registry`
  (`test_skill_policy.py:101`) is the good kind of paranoid: if
  `UnclassifiedSkill` ever became `register=True`, the runtime-refusal tests
  would silently invert, and this test plus the registry-equality test both
  catch it.
* The tripwire's own tripwire — `unclassified_skills({**SKILL_TYPES,
  'wipe_surface': Skill})` (`test_skill_policy.py:40-44`) — is synthetic, but
  it is the *right* synthetic: it exercises the exact code path a real
  registration takes, without polluting the process-wide registry for whatever
  test runs next. Combined with `test_every_skill_the_suite_exercises_is_a_registered_one`
  (`:47`), a genuinely new registered skill fails **three** tests. I am
  satisfied the chain is proven end to end and see no need for a real
  registration inside the suite.
* `EVERY_SKILL` is now derived (`safety_fixtures.py:63-69` off `EXAMPLE_SKILLS`,
  pinned equal to `SKILL_TYPES`), so the parametrised suites cannot silently
  shrink or go stale. `implementation.md:164` describes this as "derived from
  `SKILL_TYPES`" — it is derived from a hand-written dict *pinned* to
  `SKILL_TYPES`, which is equivalent in effect; not worth a change, worth
  knowing while reading the doc.

**Residual coverage gap:** the converse flag→field check (N1). That is the only
delta-related assertion I would add.

---

## Summary

| id | severity | one-line |
|---|---|---|
| N1 | NOTE | flag⇒field is asserted, field⇒flag is not: a pose-carrying skill classified `SkillPolicy()` is geometry-unchecked with every test green |
| N2 | NOTE | policy is keyed by the carried wire name, so a wrong-shaped name-squatter escapes `filter` as `AttributeError` instead of a structured refusal |
| N3 | NOTE | the raise-vs-return rule is coherent but written nowhere: `filter` documents one of its three raise paths, and the guard's two obligations live in `layer.py`, not on the protocol |

The delta is clean on everything it was sent back to fix: B1's hole is closed
at both development time and runtime, N3's mirror check is in place and tested,
N4's rule now lives on the type, and N5 fails loud without trapping the default
path. None of the three notes above blocks the merge.
