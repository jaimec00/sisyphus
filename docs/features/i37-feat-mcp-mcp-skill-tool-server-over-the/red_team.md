# red_team — i37 feat(mcp): MCP skill-tool server over the Mock backend

Reviewer: red-team (read-only). Reviewed: `src/robot_mcp/**` at HEAD `2869fdb`
(commits `c14645e`, `ec025a7`, `4e8f0bb`, `2869fdb`), against issue #37
criteria 1–6, manager rulings R1–R12, and CLAUDE.md invariants.

## Verdict

**BLOCK: none.** This is ready to merge as far as I can judge it. The seam is
consumed, not duplicated; every tool result is the skill layer's own dict passed
through untouched; no path I could find escapes the handler; the schema
derivation fails loudly rather than guessing; and the test suite is genuinely
adversarial rather than a transcript of today's output.

Below: what I verified (so the manager can see the review had teeth), then the
NOTE-level follow-ups.

---

## What I verified

### Criterion 2/3 fidelity — the dict is verbatim
`server.py:126` → `_result(payload)` (`server.py:80-90`) puts the *same object*
returned by `SkillResult.to_dict()` / `Observation.to_dict()` into
`structuredContent`, and `json.dumps` of that same object into the one text
block. No filtering, no key rewriting, no re-parse. The low-level SDK does not
post-process it either: I read
`.pixi/envs/default/lib/python3.12/site-packages/mcp/server/lowlevel/server.py`
and `tools/call` is dispatched straight to `on_call_tool`; there is no
structured-content re-serialization on this path (that lives only in the
high-level `MCPServer`, which R3 correctly avoided). `mcp_fixtures.payload`
(`test/mcp_fixtures.py:35-51`) re-checks `json.loads(text) ==
structured_content` on *every* assertion in the suite, and the stdio test does
it again after a real JSON round trip (`test_stdio_transport.py:60`).

### Nothing escapes the handler
I traced every path in `SkillToolRouter.call_tool` (`server.py:121-159`):

| input | path | outcome |
|---|---|---|
| unknown tool name | `server.py:147-149` | `ToolCallError` → `isError` |
| `arguments=None` | `server.py:126` `or {}` | missing-key `SerializationError` → `isError` |
| `arguments` not a mapping (e.g. `['a']`) | `**arguments` raises `TypeError` | catch-all `server.py:132` → `isError` |
| non-str keys in `arguments` | `ensure_mapping` → `SerializationError` | `isError` |
| `skill` smuggled in | `server.py:150-155` | `isError` |
| args to `reset`/`get_observation` | `server.py:162-167` | `isError` |
| backend raising (`TypeError`, anything) | catch-all | `isError`, session survives |
| non-JSON-serializable payload | `json.dumps` is *inside* the try (`server.py:126`) | catch-all → `isError` |

The catch-all is `except Exception`, so `asyncio.CancelledError` /
`BaseException` still propagate — which is correct, not a hole: swallowing
cancellation would break shutdown. `list_tools` (`server.py:117-119`) has no
guard, but it can only fail if pydantic rejects already-validated `Tool`
objects, i.e. never at runtime.

### Schema derivation (R4) is a real derivation, not a snapshot
- Unmapped types raise `UnsupportedFieldType` (`schemas.py:123`) and the
  catalogue is built at import (`tools.py:114`), so the failure is at import,
  not at call time. Verified there is no permissive-`{}` fallback anywhere.
- The `required` lists are checked *against the real parser* key by key
  (`test_schemas.py:98-112`): for each property, omit it and assert
  `SerializationError` iff it is in `required`. That is the anti-drift test
  that matters, and it is not a tautology.
- `test_schemas.py:151-155` parametrizes `'Side'` (a *string* annotation) as
  unmapped — which means if `robot_skills` ever adopts
  `from __future__ import annotations`, this package breaks loudly instead of
  silently emitting nothing. Nice catch by the implementer.
- `test_schemas.py:204-218` calls every listed tool with a sample generated
  *from its own published schema* and asserts the seam accepted it
  (`result['skill']['skill'] == tool.name` would `KeyError` on an error
  envelope). That closes the "published schema the seam rejects" loop in the
  direction that matters.
- Cross-checked the derived `required` against the seam by hand:
  `NavigateTo(location)`, `MoveGripper(side,pose)`, `Grasp(object_id[,side])`,
  `Place(pose[,side])`, `ExtendColumn(height)`, `Open/CloseGripper(side)` —
  all match `check_keys(...)` in `skills.py:171,196,231,267,292,312`.
  `Pose` required `('position',)` matches `geometry.py:177`.

### The `skill`-key guard (the implementer's judgement call) is correct
`server.py:150-155` refuses any `skill` argument before the merge at
`server.py:156`. Without it, `call_tool('grasp', {'skill': 'navigate_to',
'location': 'kitchen'})` would have run `NavigateTo` under the `grasp` tool —
a confused deputy, and the seam cannot catch it because the seam never learns
which *tool* was invoked. I looked for other injection paths through
`{SKILL_KEY: name, **arguments}`: every other key lands in the payload and is
rejected by `check_keys` unknown-key handling (`serialization.py:208-213`), and
the two fixed tools reject *all* arguments. The guard is complete. It does not
violate R5's "no parallel validation" — it validates a fact only this layer
knows.

### Concurrency (R10)
The lock is held across `execute` (`server.py:157-158`) and across both
`get_observation`/`reset` (`server.py:142-145`). `to_dict()` happens outside
the lock, which I checked is safe: `MockBackend.get_observation`
(`mock_backend.py:153-155`) explicitly returns an immutable snapshot of frozen
dataclasses/tuples, so there is no torn read. No mutation path bypasses the
lock.

### Scope discipline
`src/robot_skills/**` and `src/robot_backends/**` are untouched (working tree
clean; the only branch commits are the manager's dependency commit and three
`robot_mcp`/docs commits). `pixi.toml` contains exactly the manager's
`[pypi-dependencies] mcp = ">=2.0.0, <3"` and nothing else. No cancellation,
no `/stop`, no new `FailureCode`, no async execution model, no ROS dep
(`package.xml:10-17`, per R12). `.pytest_cache`/`__pycache__` are gitignored,
not committed.

### Criterion 6
`README.md:47-57` — `PYTHONPATH=<repo>/src/robot_skills:<repo>/src/robot_backends:<repo>/src/robot_mcp pixi run --frozen python -m robot_mcp`
is consistent with what actually exists: `src/robot_mcp/robot_mcp/__main__.py`
calls `server.main()`, which is the same callable the `robot_mcp_server`
console script names (`setup.py:21-25`), and the secondary path
(`install/robot_mcp/lib/robot_mcp/robot_mcp_server`) matches
`setup.cfg:2-4`. The command is plausible as written.

---

## Test adequacy (my explicit assessment)

**Adequate — and better than most.** The suite is oracle-based, not
snapshot-based: every result assertion compares against a second, identically
seeded `MockBackend` stepped through the same skills
(`conftest.py:25-34`, `test_tool_calls.py:33,36,40,45,104,132,175`). Drop a
key, add a field, wire a tool to the wrong skill class, or let the server's
backend drift, and these fail. I checked each required behaviour would actually
regress-fail:

| behaviour | test | fails on broken code? |
|---|---|---|
| required sequence, verbatim dicts | `test_tool_calls.py:24` | yes (parallel oracle) |
| text block round-trips | `mcp_fixtures.py:42-50`, on every call | yes |
| refusal is not an error | `test_tool_calls.py:120` | yes (`is_error`, `code`, dict-equal) |
| malformed args → tool error | `test_tool_calls.py:148` (6 shapes) | yes (label + seam message + session survives) |
| unknown tool | `test_tool_calls.py:178` | yes (would raise `MCPError` if unhandled) |
| `skill` smuggling | `test_tool_calls.py:191` | yes (world asserted unchanged) |
| catch-all | `test_tool_calls.py:213` (backend that raises) | yes |
| tool set == `SKILL_TYPES` ∪ fixed | `test_schemas.py:62` | yes |
| new field type breaks the build | `test_schemas.py:187` | yes |
| stdio framing end to end | `test_stdio_transport.py:40` | yes — real subprocess, real pipes, `stdio_client` + `ClientSession`, initialize + list + 3 calls + an error call |
| no ROS at runtime | `test_no_ros_runtime.py` | yes (clean subprocess + self-tested AST scanner) |

The one test that is *not* load-bearing is the concurrency one — see NOTE 1.
Everything else earns its place.

---

## NOTES (follow-ups, not blockers)

**NOTE 1 — the concurrency test cannot fail if the lock is deleted.**
`src/robot_mcp/test/test_tool_calls.py:232-262` documents itself as "the
interleaving the router's lock exists for", but `MockBackend.execute`
(`mock_backend.py:175`) is fully synchronous: with the lock removed, the body
of `_payload` (`server.py:156-159`) contains no `await` between
`skill_from_dict` and `to_dict()`, so no interleaving is possible and
`['failed','failed','failed','ok']` still holds. Scenario: someone deletes
`server.py:115/142/157` in a refactor — suite stays green. The test still
proves something real (four in-flight calls share *one* backend and one
consistent world), so this is not a coverage hole for any acceptance criterion;
the docstring just overclaims. Fix direction: either retitle it to what it
proves ("all calls share one backend"), or make the lock testable by injecting
a backend stub whose `execute` yields (see NOTE 2, which would make the lock
genuinely load-bearing and testable at the same time).

**NOTE 2 — a blocking backend will freeze the whole server (extensibility,
forward-looking).** `server.py:158` calls `self._backend.execute(skill)`
directly on the event loop. That is fine for the Mock (microseconds), but
`RobotBackend.execute` is a synchronous ABC method, so the first Sim/Real
backend that takes seconds will block the event loop entirely — the process
cannot read stdin, answer a ping, or even report progress while a `navigate_to`
runs. `await anyio.to_thread.run_sync(self._backend.execute, skill)` (still
inside the existing lock) keeps the call semantics identical, is not an "async
execution model" in the out-of-scope sense, and is what turns R10's lock from
decoration into protection. Worth an issue follow-up rather than a change here,
since it only bites when a non-Mock backend lands.

**NOTE 3 — the "a skill with no docstring fails the build" guard is dead code.**
`src/robot_mcp/tools.py:81-87` raises if `inspect.getdoc(skill_type)` is falsy,
and `implementation.md:128-129` states this as a guarantee. It cannot fire for
a real skill: `@dataclass` sets `cls.__doc__` to the generated signature string
when a class has none, and `inspect.getdoc` additionally inherits docstrings
from the MRO since 3.5. Scenario: someone adds
`@dataclass(frozen=True) class WipeSurface(Skill): name='wipe_surface';
target: str` with no docstring — the shipped tool description becomes
`WipeSurface(target: str)` + the result note, i.e. an agent-facing description
that is a Python signature. Fix direction: check
`skill_type.__dict__.get('__doc__')` (own docstring only) before falling back,
and add the missing negative test alongside `test_schemas.py:187`.

**NOTE 4 — the dispatch set and the advertised set are read at different
times.** `tools.py:114` snapshots `SKILL_TYPES` at import, but
`server.py:147` tests membership against the *live* `SKILL_TYPES`
`MappingProxyType`. If a skill is ever registered after `robot_mcp.tools` is
imported (an out-of-repo skill module, or a lazily imported one), the server
will happily execute a tool it never advertised, and — worse —
`_check_name_collisions` (`tools.py:67-78`) will not have seen it, so a
late-registered skill named `reset` would be silently shadowed by the fixed
tool at `server.py:140` instead of raising. Not reachable today (all seven
skills are registered when `robot_skills` is imported, which `tools.py:24`
does before building). Fix direction: dispatch off the same snapshot the
catalogue was built from (e.g. a `{name: skill_type}` dict built in
`build_tools`), so advertised set == callable set by construction.

**NOTE 5 — `_RECORD_REQUIRED`'s `Quaternion` row is not checked against the
real parser.** `schemas.py:65-69` is validated key-by-key for `Pose` and
`Point` by `test_schemas.py:114-134`, but nothing asserts that
`Quaternion.from_dict` really requires all of `x,y,z,w`
(`geometry.py:119`). Scenario: `w` gains a wire default in the seam; the table
silently over-constrains the published schema and an agent is told to send a
key the seam no longer needs. Fix direction: extend the loop at
`test_schemas.py:129-134` over `orientation` as well.

**NOTE 6 — `test_no_ros_runtime.py:65` hard-codes the tool count (`'9'`).**
Adding an eighth skill to the seam — the exact scenario the package advertises
as needing no edit here (`test_schemas.py:173`) — makes this unrelated test
fail. Fix direction: have the probe print/compare against
`len(robot_mcp.tools.TOOLS)`, or assert `count == len(SKILL_TYPES) + 2`.

**NOTE 7 — `{'type': 'string'}` is looser than the seam for identifiers.**
`schemas.py:54-57` maps `str` to a bare string schema, but `as_identifier`
(`validation.py:36-42`) rejects blank/whitespace-only strings — so
`{"object_id": ""}` is schema-valid and seam-rejected (the suite even relies on
this at `test_tool_calls.py:170`). Harmless (it surfaces as a clean tool error)
but `"minLength": 1` would make the published schema honest and let a
schema-aware client catch it before the call.

**NOTE 8 — the stdio test can hang instead of failing.**
`test_stdio_transport.py:44-45` builds a `ClientSession` with no read timeout,
so a server that starts but never answers (e.g. a future `print()` on stdout
corrupting the framing) blocks the test forever rather than failing it — the
one failure mode most likely to hit CI on a Pi. Fix direction: pass a
read/initialize timeout, or wrap the body in `anyio.fail_after(...)`.

**NOTE 9 — `mcp` is not declared as a dependency of the package itself.**
`setup.py:14` is `install_requires=['setuptools']` and `package.xml:11-12`
declares only the two workspace packages, yet `server.py:36-38` imports
`mcp`/`mcp_types`. It works because pixi provides it, and there is no rosdep
key for `mcp` (so `package.xml` is arguably the wrong place), but
`install_requires=['setuptools', 'mcp>=2.0.0,<3']` would make the ament package
self-describing. Follows repo convention as-is; flagging for the record.

**NOTE 10 — two doc nits.** (a) The in-process snippets at `README.md:97` and
`robot_mcp/__init__.py:28` use `async with` at module level; copy-pasted
verbatim they are a `SyntaxError`. (b) The root `README.md:13-14` package list
does not mention `robot_mcp` — out of this feature's owned paths, so it belongs
in an operational follow-up rather than here.
