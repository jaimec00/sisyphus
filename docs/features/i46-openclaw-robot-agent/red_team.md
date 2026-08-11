# Red team — #46 First milestone: OpenClaw `robot` agent → robot_mcp → Mock

Read-only review of `feat/i46-first-milestone-openclaw-robot-agent-rob` against
issue #46's acceptance criteria, R1–R12 in `status.md`, CLAUDE.md's
architectural invariants, and D4/D17/D18/D19/D21/D22 + `PROJECT.md`.

Bottom line up front: the safety wiring is correct and the test suite behind it
is genuinely strong — I tried hard to break `_gated_execute` and
`default_safety_layer` and could not. Every BLOCK below is about a **claim that
outruns its evidence** on a safety path, not about broken production code. Two
of the three are fixed with text; one is a one-line prompt sentence plus a
loosened assertion.

---

## BLOCK

### B1 — The keep-out guard **is** routed around, and the test that says it is not proves the opposite

`src/robot_mcp/test/test_clear_the_table.py:249-285`
(`test_an_injected_guard_stops_the_loop_and_the_agent_cannot_route_around_it`),
docstring at `:250-254` and comment/assert at `:283-285`.

**The claim.** The docstring says *"no sequence of tool calls the agent can make
gets the object into the region"*, and line 283 comments *"The object never
entered the region: it is still in a gripper."*

**Why it is false, with the arithmetic.** The injected box (`:258-265`) is
`x ∈ [1.90, 2.90]`, `y ∈ [-0.50, 0.50]`, `z ≥ 0.45`. The driver grasps at the
table, then calls `navigate_to('kitchen')` (`:273`). `NavigateTo` carries
`SkillPolicy()` with `has_cartesian_target=False`
(`src/robot_safety/robot_safety/policy.py:88`), so `target_pose()` returns
`None` and `KeepOutBoxGuard.check` returns `None` without looking
(`collision.py:128-130`). Meanwhile
`MockBackend._carry_held_objects` (`mock_backend.py:360-365`) re-poses the held
object to the gripper's world pose after that successful navigate:

* grasp at `table` → arm offset `= book_1.pose − shoulder = (0.30, ∓0.08, −0.05)`;
* at `kitchen` the left shoulder is `(2.00, 0.18, 0.80)` (right: `(2.00, −0.18, 0.80)`);
* held object pose `= (2.30, ±0.10, 0.75)` — **inside the forbidden box on all
  three axes**, for either arm, and for either table object.

So by the time line 276 issues the `place`, the object has already been carried
into the keep-out region, and the very payload the test inspects
(`refused['observation']`, which is the pre-call observation the abort path
returns) contains it there. Line 284-285 asserts `held_by is not None` — it
checks *who is holding it*, never *where it is* — so the assertion cannot
contradict the comment above it, and a reader concludes the guard is escape-proof
when it demonstrably is not.

**Failure scenario this misleads someone into.** A future feature turns
`below_floor` into a real keep-out (stove, staircase, the dog's bed) and relies
on this test's stated property. The agent grasps an object, drives, and the
object — and the arm holding it — passes straight through the region with no
verdict taken, because only `move_gripper` and `place` are geometry-checked.
Nothing in `robot_mcp/README.md`'s otherwise-excellent "what actually bites
today" section (`:72-88`) mentions that a *carried* object and a *driving base*
are outside the guard's scope; it says the geometry checks "the *goal* pose",
which is true but does not connect to "so a chore can move an object anywhere".

**Fix direction.** Do not weaken the test — it correctly proves the abort path.
Fix the two false statements and add the true one:
1. rewrite the docstring to what is proven: *a `place` into the region is
   aborted, twice, and the object stays held*;
2. replace the line 283 comment, or better, assert what is actually true and
   interesting: that the carried object's pose **is** inside the box while
   `place` is refused — a one-line assertion that turns a misleading comment
   into a documented limitation;
3. add one sentence to `robot_mcp/README.md`'s safety section: the guard judges
   commanded target poses only, so `navigate_to` (and therefore anything the
   robot is carrying) is not geometry-checked;
4. record it as a follow-up alongside F8 (real collision geometry needs a swept
   volume and the base's route, which is exactly the MoveIt work D-invariant 5
   defers).

---

### B2 — `reset` is handed to an autonomous LLM, ungated, with no prohibition — and a test makes that exposure mandatory

`src/robot_brain/robot_brain/openclaw/openclaw.robot.json:27`,
`src/robot_brain/test/test_openclaw_config.py:144-147`,
`src/robot_brain/robot_brain/openclaw/AGENTS.md:47`.

**What is on disk.** `toolFilter.include` lists `reset`; the prompt's tool table
describes it as *"Restore the seed world. A test/demo tool: it undoes
everything."* — a description, not a prohibition; and the prompt says nothing
anywhere else about when not to call it. `reset` is routed in
`server.py:232-238`, i.e. through the **non-skill branch**, so it never touches
`SafetyLayer` (correctly — it is not a skill — but it *is* a state-destroying
command).

**Failure scenario.** Jaime texts "clear the table". The agent grasps `book_1`,
gets an `out_of_reach` on a place, and — having been told it may "start over"
and that `reset` "undoes everything" — calls `reset` to get a clean slate.
Against Mock that silently teleports both objects back onto the table and empties
the grippers mid-chore; the agent then reports the table cleared, or loops.
Against the Sim/Real backend this same tool boundary is meant to front (D9,
`README.md:157-160`), `RobotBackend.reset()` is documented as *"Return the robot
and the world to their initial state"*
(`robot_backends/robot_backends/interface.py:33-38`) — an ungated, unbounded
motion command, issued by the LLM, at the one seam D21 says enforcement must sit
below.

**The extensibility trap, which is the worse half.**
`test_the_exposed_tools_are_the_tools_that_exist` asserts
`exposed == set(TOOL_NAMES)` (`test_openclaw_config.py:147`). Its docstring says
*"The tool filter cannot name a tool the server does not serve"* — that is the
`⊆` direction. The `⊇` direction it also asserts means **every tool
`robot_mcp` ever grows is forced into the LLM's allowlist or the suite goes red**.
The path of least resistance for the next person who adds a tool (a teleop
escape hatch, a torque setter, a `home_arms`) is to expose it to the model. A
test should not push future changes toward the unsafe option.

**Fix direction.**
1. One sentence in `AGENTS.md`: never call `reset` while doing a chore; it is
   only for Jaime explicitly asking to start over. (Cheap, and it matches the
   care the prompt already takes with `rejected` at `AGENTS.md:117-122`.)
2. Split the config assertion: keep `exposed <= set(TOOL_NAMES)` as the
   no-phantom-tool guard, and assert the *skill* tools plus `get_observation`
   are all exposed, with any exclusion listed explicitly in a named constant so
   omitting a tool is a deliberate, reviewed decision rather than a test failure.
3. If `reset` is to stay exposed (defensible against Mock today), say so in
   `robot_brain/README.md` as a Mock-only decision to revisit before Sim/Real.

---

### B3 — The mandatory server-side guards are missing, and the only place the loop is bounded is the prompt

`src/robot_brain/robot_brain/openclaw/AGENTS.md:25-26`;
absence in `src/robot_mcp/robot_mcp/server.py` and
`src/robot_mcp/README.md:162-170`.

`PROJECT.md:45` is unambiguous: *"Guards (mandatory for a home robot) live
server-side, below the tool boundary — never trusted to the LLM: max-steps +
timeout + stuck-detection; user stop/cancel; heartbeat/dead-man; e-stop; one
task at a time."* This PR is the first change that actually puts an LLM in the
loop, and the only stuck-detection it ships is
`AGENTS.md:25-26`: *"three failed attempts at the same step is a report to
Jaime, not a fourth attempt"* — i.e. in the prompt, exactly where PROJECT.md
says it must not be. The only max-steps in the change is `MAX_STEPS = 8` in a
*test* (`test_clear_the_table.py:48`).

I do **not** think building those guards belongs in #46 — they are D16-deferred
and the issue is scoped to the seam. What is not acceptable is that this is the
one place the PR stops being honest about what does not exist. `robot_mcp`'s
README has a *"Deliberately absent"* section (`:162-170`) that carefully explains
the missing cancellation and e-stop and says why; it says nothing about
max-steps, timeout, stuck-detection or one-task-at-a-time. `status.md`'s F1–F9
do not record them either. A reader of this PR — including Sisyphus deciding to
merge — is given the impression that the guard story is complete because the
safety layer is wired.

**Failure scenario.** An agent that misreads a refusal loops
`navigate_to`/`grasp`/`place` indefinitely (each call under the 120 s
`requestTimeoutMs`, which bounds a *call*, not a *chore*), and nothing
server-side stops it; two Telegram messages start two chores against one
`SkillToolRouter`, whose `anyio.Lock` serializes individual calls but knows
nothing about tasks, so the two interleave into one incoherent world.

**Fix direction.** Text only: add max-steps/timeout/stuck-detection and
one-task-at-a-time to `robot_mcp/README.md`'s "Deliberately absent" with the
same honesty as the cancellation paragraph (they are PROJECT.md-mandatory, not
yet built, and the prompt sentence is defence in depth rather than the guard),
and record a follow-up so Sisyphus files it. Keep the prompt sentence.

---

## NOTE

### N1 — `default_safety_layer`'s `try/except` is correct today but is scoped by exception *type* across a package boundary
`src/robot_mcp/robot_mcp/server.py:160-164`. The docstring's claim holds (see
"cleared" below), but it holds only because `KeepOutBoxGuard.from_limits`
happens to have exactly one `raise` today
(`robot_safety/collision.py:120-124`). If `robot_safety` ever adds a second
`SafetyConfigError` inside `from_limits` (validating box geometry, say), a
non-default limit set passed to `default_safety_layer(limits=...)` degrades
silently to `NullCollisionGuard`. The shipped-defaults path is protected by
`test_the_default_server_enforces_the_configured_keep_out_geometry`, so the real
exposure is small. Cheapest fix removes the coupling entirely:

```python
guard = NullCollisionGuard() if not limits.keep_out_boxes \
    else KeepOutBoxGuard.from_limits(limits)
```

— nothing is swallowed at all, and the "any other error still raises" claim
becomes structural rather than a fact about another package's current source.

### N2 — "the table is clear" is really "nothing graspable within 1 m of the base", with 0.19 m of margin
`test_clear_the_table.py:45,74,152`. `NEARBY = 1.0`; `book_1` and `cup_1` sit
0.814 m from the `table` stand point. Move a table object 0.20 m further out (or
raise the column, which does not move objects but does move the base-relative
geometry a future edit might key off) and the milestone's headline assertion
`clutter(final['observation']) == []` starts passing on an uncleared table. The
`x ≈ 2.0 ± 0.6` check at `:160` partly compensates. Consider asserting the
positive form as well — every id in `expected` is near the *kitchen* base — so
"cleared" cannot be satisfied by an object merely drifting out of radius.

### N3 — Both table objects are placed at the identical pose
`test_clear_the_table.py:79-90` derives the drop pose from the nearest object
labelled `counter`, which is always `counter_1`, so both objects end at exactly
`(2.40, 0.00, 0.55)`. Harmless in Mock (no collision model) and the prompt
teaches the same recipe (`AGENTS.md:148`), but it means the "clear the table"
proof stacks two rigid bodies in one point. Worth a small offset in the driver,
and a line in the prompt about not putting a second object where the first one
went, before a Sim backend makes this a real failure.

### N4 — `tools.allow: ["mcp__robot__*"]` may configure away the "reports back in natural language" half
`openclaw.robot.json:43-47`, pinned by `test_openclaw_config.py:86-90`
(`all(MCP_SERVER_NAME in entry for entry in allowed)`). If OpenClaw's
`tools.allow` is a strict allowlist over *all* tools rather than only MCP ones,
and a reply/message tool exists in that namespace, the agent would be able to
drive the robot and unable to answer Jaime — which is exactly half of issue
#46's acceptance criterion. Unverifiable from this laptop (R8), and
`README.md:108-110` flags the *spelling* of `mcp__robot__*` but not this
semantic risk. Add "can the agent still reply?" to README step 6's list of
things to check on the Pi; step 7 would catch it, but only after the fact.

### N5 — `bash -lc` was never part of what was actually verified
`openclaw.robot.json:8-14` launches `ssh -T laptop bash -lc "…"`. The
implementer verified (correctly, and says so) that `pixi`'s manifest warning
goes to stderr — but the command they ran was the README's direct form, not
`bash -lc`. A login shell sources `/etc/profile` and `~/.bash_profile`; any
`echo`, MOTD or version-manager banner in those goes to **stdout** and corrupts
the first MCP frame. `robot_brain/README.md:91-98` (step 4) is the right check
and says "no banner, no motd" — this is only a note that the risk is specific to
`-l`, and that dropping `-l` (or using `bash -c` with an explicit `PATH`) would
remove it.

### N6 — The prompt vocabulary guard is a bag of words, not a grammar
`test_prompt_drift.py:61-85`. `live_vocabulary()` flattens every wire key at
every depth into one set, so `` `objects[].pose.position.w` `` would pass (`w`
is a quaternion key) even though `position` has no `w`. The guard is
deliberately word-level and its docstring says so; flagging only so nobody reads
it as a schema check. It does catch every mutation it claims to (I re-derived
the mutation table: an invented tool, an invented field name, a renamed enum
value all leave the set).

### N7 — `_gated_execute` costs one extra `get_observation()` per skill call
`server.py:276`. Already called out in `implementation.md`; free against Mock,
not against a Sim/Real backend whose perception is a render. Noting it so it
survives into a follow-up rather than only into a deleted doc.

---

## Hypotheses I checked and cleared

Explicitly, so you know what was tested and not merely unmentioned.

1. **R12 swallows more than "no regions configured"** — **wrong, cleared.**
   `KeepOutBoxGuard.from_limits` (`collision.py:110-124`) has exactly one
   `raise`, and it is the emptiness check. The subsequent `cls(boxes=…)` can
   only raise `TypeError` (`collision.py:101-108`), which is not caught, and
   cannot raise at all because `SafetyLimits.__post_init__`
   (`limits.py:306-311`) has already type-checked every box. A *malformed
   limits file* raises inside `SafetyLimits.defaults()` / `from_yaml`, which is
   outside the `try` (`server.py:158-161`). The docstring's claim at
   `server.py:150-153` is accurate. See N1 for the residual coupling.
2. **R1's abort path** —
   (a) **stale/aliased observation: cleared.** `MockBackend.get_observation`
   (`mock_backend.py:153-173`) constructs a fresh immutable `Observation` from
   scratch on every call; nothing in the returned tree aliases the backend's
   mutable `_objects`/`_grippers`. Because nothing executes on the abort path,
   the sampled observation and the post-call world are equal by construction —
   and `test_an_aborted_call_never_reaches_the_backend`
   (`test_safety_gate.py:228-247`) pins `result['observation'] == before ==
   after` against a `RecordingBackend` with an empty execute log, which is the
   right proof.
   (b) **reporting the original skill on abort: correct.** Nothing ran, so there
   is no rewritten skill to report; `test_safety_gate.py:261-274` asserts the
   echoed skill is the one the agent sent.
   (c) **empty or lossy composed `reason`: cleared.** `SafetyEvent.detail` is
   validated non-blank by `as_identifier` (`events.py:79-80`,
   `validation.py:36-42`), so `'; '.join(notes)` is never empty and
   `SkillResult.__post_init__`'s "a failed result must carry a non-empty reason"
   (`result.py:176-177`) cannot fire. `dataclasses.replace` does re-run
   `__post_init__` (validation only, no mutation), and the clamped-then-refused
   case keeps `status`, `code` and both prose halves —
   `test_a_clamped_command_the_backend_still_refuses_keeps_both_stories`
   (`test_safety_gate.py:204-225`) is a real test of exactly that, using a world
   whose column stops below the safety limit.
3. **R3's "no ungated server" claim** — **holds.** `build_server`, `run_stdio`
   and `main` all funnel through `SkillToolRouter.__init__`, which is the single
   `isinstance(safety, SafetyLayer)` chokepoint (`server.py:195-199`), and
   `_payload`'s skill branch has no path to `self._backend.execute` that skips
   `_gated_execute` (`server.py:248-251`). `test_the_gate_is_per_server_and_
   cannot_be_switched_off` (`:277-292`) covers `SkillToolRouter(backend)`,
   `SkillToolRouter(backend, None)` and both duck-typed-stub rejections. No test
   in the suite builds a router that bypasses the gate; `mcp_fixtures.connected`
   defaults `safety=None`, so every test that does not opt in is driving the
   *default* gate — which is why the milestone tests count.
4. **The milestone test passes vacuously or flakily** — **wrong on every
   sub-hypothesis.** `clutter()` cannot be empty on the first pass:
   `test_clear_the_table.py:142-143` asserts `len(expected) >= 2` *before* the
   loop, and `:149` asserts `sorted(put_away) == sorted(expected)`, so the
   emptiness assertion at `:152` cannot be trivially true. `MAX_STEPS = 8`
   counts *rounds*, not calls: two objects need three rounds. Safety is in the
   path for real, not merely shaped like it — `:218-246` uses the **default**
   server (nothing injected) and asserts the clamped height, the clamped
   observation, and that an in-range follow-up carries `reason is None`; the
   abort test uses a `RecordingBackend` elsewhere to prove the backend never
   saw the command. The tie in `clutter`'s sort (both objects are exactly
   0.8139 m from the table stand point) resolves deterministically because
   `sorted` is stable and `get_observation` emits objects id-sorted. See N2 for
   the one real fragility.
5. **Prompt drift guards are vacuous** — **wrong, cleared, and they are the
   strongest part of the change.** Nothing iterates an empty set:
   `test_the_tool_table_lists_exactly_the_served_tools` is a set *equality*
   against `TOOL_NAMES`; `test_the_table_teaches_exactly_the_schema_properties`
   is parametrized per tool and compares against
   `tool.input_schema['properties']`; `section()` **raises** on a missing
   heading rather than returning `''` (`brain_fixtures.py:62-74`) — that is
   precisely the vacuity trap, and it was closed deliberately. Safety numbers
   are compared as **floats**, both directions
   (`test_prompt_drift.py:148-161`), so a reformat cannot slip through and a
   number with no limit behind it fails too. `required` **is** read — both in
   `SCHEMAS` (`:39-45`) and in `test_the_table_says_which_arguments_are_optional`
   / `test_every_example_call_would_deserialize_at_the_tool_boundary`
   (`:122-142`) — so a newly-required argument the prompt omits fails twice. The
   "names nothing that does not exist" check is not satisfied by any backticked
   word: the allowed set is derived from a live `SkillResult.to_dict()`, the
   live schemas, the live enums and the live seed world.
6. **The secret scanner is theatre** — **wrong.** `TOKEN_SHAPE`
   (`test_openclaw_config.py:38`) is a real Telegram-token shape, and
   `test_the_fragment_contains_no_secret` proves the detector fires on a
   token-shaped sample *before* trusting its negative (`:106`). That is the
   right construction and it earns its commit.
   `toolFilter.include` **is** asserted equal to `TOOL_NAMES`, not merely a
   subset — see B2 for why the equality is the problem rather than the
   reassurance.
7. **R4 oversells the three dead checks** — **wrong; the honesty is exemplary.**
   `robot_mcp/README.md:72-88` names e-stop, velocity and gripper force as
   "wired and reachable, but silent until a backend measures something" and
   calls the alternative "safety theatre"; `robot_safety/README.md:76-82` says
   the same; the prompt states only the *numbers* (which are true limits) and
   never claims the checks fire. I found nothing overselling anywhere. B3 is
   about a different gap (the PROJECT.md:45 guard list), not about R4.
8. **R10's world change** — **verified independently, arithmetic confirmed.**
   `cup_1` at `(0.30, 1.90, 0.75)`; from the `table` stand point at the 0.30 m
   start column the left shoulder is `(0, 2.18, 0.80)` → 0.4134 m, right
   shoulder `(0, 1.82, 0.80)` → 0.3145 m; `book_1` is 0.3145 / 0.4134 m the
   other way round. Both well inside the 0.85 m reach, so the docstring's
   "furthest ~0.41 m" is right and no `extend_column` is needed. Grasping is
   deterministic (`SIDE_ORDER` first free *and* reachable side —
   `mock_backend.py:400-414`), and the second grasp lands on the other arm,
   which also reaches. `cup_1` collides with nothing: it is 0.20 m from
   `book_1`, and `TABLE_DROP` (`mock_backend_fixtures.py:25-27`) is
   `(0.35, 2.05, 0.75)`, distinct from both. I found no existing assertion whose
   meaning changed — `test_mock_scenario.py`'s "nothing else in the scene moved"
   loop (`:59-63`) is object-set-agnostic, no golden fixture encodes the world,
   and `test_two_backends_are_bit_identical…`'s `Grasp('book_1')` after
   `ExtendColumn(0.8)` still reaches (0.63 m).
9. **Out-of-brief edits** — **both forced, neither a loosening.**
   `scripts/tests/test_ratchet.py:316-334` moved `robot_brain` from the
   skeleton list to the has-implementation list and *added* an assertion
   (`assert modules['robot_brain']`); the guard's behaviour is untouched and
   the change is strictly stronger. `scripts/test_baseline.json` was re-cut
   upward for every package (`robot_safety 0 → 176` corrects a stale floor,
   `robot_brain 0 → 37`, `robot_mcp 52 → 71`, `robot_backends 59 → 60`) — a
   ratchet raised, never lowered. `robot_safety/README.md:76-86` replaced a
   sentence this feature made false with an accurate one that *understates*
   coverage ("Callers that bypass MCP are not gated today"). No test was
   softened anywhere in the diff that I can see.

---

## Verdict

Against what is on disk, issue #46's criteria 1–3 are met and criterion 4 is met
to the fullest extent this laptop can establish. The safety gate is genuinely
unbypassable at the tool boundary (single chokepoint, type-checked, no env var,
no code path), the clamp and abort verdicts ride inside the unchanged
`SkillResult` wire form exactly as R1 required, the default server enforces the
shipped `limits.yaml` end to end including its keep-out geometry, and
`test_clear_the_table.py` closes the whole chore over a real in-process MCP
client using nothing but the returned payloads — which is the strongest
stand-in for the LLM that can be built here. The prompt-drift suite is the best
piece of work in the change: it is a type-checker for prose, it derives every
expected value from the live seam, and it closes the vacuity traps (a missing
section raises; numbers compare as floats; `required` is read as well as
`properties`) that this kind of test usually falls into. The three BLOCKs are
all statements that outrun their evidence rather than defects in the running
code: a test docstring that claims the keep-out guard cannot be routed around
when a carried object provably is (B1), `reset` handed to the model with no
prohibition and a test that makes that exposure structurally mandatory (B2), and
PROJECT.md:45's mandatory server-side guards being unaddressed and, uniquely in
this PR, undocumented as absent (B3). Fixing all three is text plus one relaxed
assertion; none should take a second round.

What **cannot** be verified from this laptop, and must not be read as verified:
that OpenClaw accepts `openclaw.robot.json` at all — the field names, the
`sandbox.mode` and `tools.allow` value vocabularies, and `bindings[].match`'s
keys are documentation-derived and untested against any OpenClaw build; that the
`ssh -T laptop bash -lc …` leg works from the Pi, including whether a login
shell keeps stdout free of banners; that the three hard-coded
`/home/sisyphus/worktrees/main` paths and the `laptop` SSH alias resolve on the
real machines; and — the important one — that **an LLM given this prompt
actually clears the table and reports back in natural language**. The milestone
test proves the loop *can* be closed from the payloads alone; it proves nothing
about the model closing it. `robot_brain/README.md:126-141` and
`implementation.md` both say this plainly and correctly, and the PR description
and "ready" signal must repeat it.
