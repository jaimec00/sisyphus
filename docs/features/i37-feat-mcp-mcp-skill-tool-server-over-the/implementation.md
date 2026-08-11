# implementation — i37 feat(mcp): MCP skill-tool server over the Mock backend

New ament_python package `src/robot_mcp/`: an MCP **stdio** tool server that
exposes the frozen `robot_skills` seam over one `MockBackend`, so an MCP-capable
agent drives the robot purely by tool calls. Nothing outside `src/robot_mcp/**`
(plus this doc) was touched; `pixi.toml`/`pixi.lock` were left exactly as the
manager committed them in `c14645e`.

## Shape

| file | role |
|---|---|
| `robot_mcp/schemas.py` | JSON Schema derivation from the skill dataclasses. No MCP import — pure data, independently testable. |
| `robot_mcp/tools.py` | The catalogue: 7 `types.Tool`s generated from `SKILL_TYPES` + the 2 fixed tools. Built once at import time. |
| `robot_mcp/server.py` | `SkillToolRouter`, `build_server(backend=None)`, `run_stdio`, `main`. |
| `robot_mcp/__main__.py` | `python -m robot_mcp` — the documented launch command, no colcon build needed. |
| `README.md` | Criterion 6: the one-liner, an MCP client config, what is deliberately absent. |

Layering is deliberate: `schemas.py` knows about skills but not MCP; `tools.py`
knows about both but not about a backend; `server.py` is the only file that
touches a backend or an event loop. Adding a Sim/Real backend later changes
nothing but the argument to `build_server` (D9).

## How each acceptance criterion is met

**1 — stdio server, one `MockBackend`, one tool per skill + `get_observation` +
`reset`.**
`build_server` (`src/robot_mcp/robot_mcp/server.py:170`) constructs exactly one
backend (`MockBackend()` by default) and hands it to a single `SkillToolRouter`
(`server.py:102`) that lives for the life of the server, so state accumulated
across calls persists and `reset` is the only way back. The transport is the
SDK's `stdio_server` (`server.py:186-190`), reached via the `robot_mcp_server`
console script (`setup.py:21-25`) or `python -m robot_mcp`
(`robot_mcp/__main__.py`). Tool names are the skills' own wire names
(`tools.py:90-112`), and the arguments are the wire keys `Skill.to_dict` writes
— asserted, not assumed, by `test_schemas.py:88`. The two non-skill tools are a
declared addendum (`tools.py:36-42`), and a future skill named `reset` or
`get_observation` fails the import (`tools.py:67`, tested at
`test_schemas.py:196`).

**2 — the structured dict, no prose.**
`_payload` (`server.py:138`) returns `SkillResult.to_dict()` /
`Observation.to_dict()` untouched, and `_result` (`server.py:80`) puts that same
object in `structuredContent`, with a verbatim `json.dumps` of it as the text
block (R6). The tests never compare against a hand-written expected dict: they
compare against a second, identically seeded backend stepped through the same
skills (`test_tool_calls.py:24`, `:92`, `:120`), so any filtering, re-ordering,
re-serialization or added field fails immediately.

**3 — refusal vs malformed call.**
A refusal never becomes an error: `MockBackend.execute` returns
`SkillResult(status='failed', ...)` and it flows out through the ordinary
success path, `isError` unset — `test_tool_calls.py:120` checks `ghost_1` →
`unknown_object` and `counter_1` → `not_graspable`, both dict-equal to the
backend's own result and both leaving the world untouched. A malformed argument
raises `SerializationError` out of `skill_from_dict` and is caught at
`server.py:127-129`, returning `isError=True` with the seam's own message plus
`{'error': 'SerializationError', 'message': ...}` (`server.py:93`); six shapes
of malformed input are covered at `test_tool_calls.py:148`, each also asserting
the session still serves the next call. Unknown tool names
(`server.py:147-149`), arguments to the argument-free tools (`server.py:162`)
and any unexpected exception (`server.py:133-137`) take the same non-fatal
path — `test_tool_calls.py:213` proves a backend that raises does not kill the
session, which the low-level SDK would otherwise turn into a protocol-level
`MCPError` and a dropped connection.

**4 — schemas consistent with the skill dataclasses.**
Derived, per R4. `skill_schema` (`schemas.py:137`) reads
`dataclasses.fields()`; `schema_for_type` (`schemas.py:104`) maps `str`,
`float`, an `Enum` (values read off the enum itself, `schemas.py:87`), the
nested `Pose`/`Point`/`Quaternion` records, and `T | None` (which yields `T`'s
schema and drops out of `required`). Every generated object carries
`additionalProperties: false`. An unmapped field type raises
`UnsupportedFieldType` (`schemas.py:123`) at import time, because `tools.py:114`
builds the catalogue at module scope — a new skill with an unknown field type
breaks the build instead of shipping a lying schema (`test_schemas.py:187`).
`test_schemas.py:173` proves the converse: a skill added to the seam becomes a
tool with no edit here.

**5 — deterministic tests, Mock only, no ROS graph.**
55 tests. The required `reset → navigate_to → grasp → get_observation` sequence
runs through a real in-process MCP client session
(`test/mcp_fixtures.py:24`, used by `test_tool_calls.py:24`), plus one real
**subprocess + pipe** round trip against the documented command
(`test/test_stdio_transport.py:40`). `test_no_ros_runtime.py` mirrors
`robot_backends`' guard: a clean-subprocess run of a whole grasp plus an AST
scan for lazy `rclpy` imports. `package.xml` is git-tracked, so
`scripts/check_test_integrity.py` audits the package; the audit passes with 55
non-skipped tests.

**6 — README + the exact command.**
`src/robot_mcp/README.md`. Both documented forms were *run* against a real MCP
client before being written down: the shell one-liner (`cd <repo> &&
PYTHONPATH=... pixi run --frozen python -m robot_mcp`) and the client-config
form using `--manifest-path` so it does not depend on the client's cwd. The
secondary built alternative (`robot_mcp_server` from the install space after
sourcing `install/setup.bash`) was verified too.

## Decisions and trade-offs

- **Low-level `Server`, not `MCPServer`** (R3). The ergonomic path derives
  schemas from Python function signatures, which cannot express
  `SKILL_TYPES`-driven registration and adds signature artifacts to the wire
  schema. The cost is that the SDK validates nothing for us — hence the
  explicit unknown-tool branch and the catch-all.
- **One text block per result, not `content=[]`** (R6). `content=[]` is legal
  in this SDK, but a verbatim `json.dumps` of the same dict is what clients that
  ignore `structuredContent` need, and it is a copy, not a second rendering.
- **No `outputSchema`** (R6). The seam's `schema_version` stamp (D18) is the
  versioning mechanism; declaring the observation schema here would be exactly
  the parallel serialization the brief forbids.
- **The `skill` key is rejected as an argument** (`server.py:150-155`). R5's
  construction is `{SKILL_KEY: name, **arguments}`, in which a caller-supplied
  `skill` key would override the tool name and let a call to `grasp` run
  `navigate_to`. Rather than silently reordering the merge (which would ignore
  the key), the router refuses the call. This is not the arg validation R5
  forbids — the seam cannot know which *tool* was called, so only this layer
  can catch the substitution. `test_tool_calls.py:191` covers it.
- **The argument-free tools refuse arguments** (`server.py:162`). Their schema
  says `additionalProperties: false` and no skill parser is involved to enforce
  it, so the handler does. Ignoring them would make the published schema a lie.
- **One `anyio.Lock` around every backend call** (`server.py:115`, `:142`,
  `:157`, per R10). Today the Mock's `execute` is synchronous, so the lock is
  not yet load-bearing; it is what keeps the invariant true the moment a backend
  gains an `await`. `test_tool_calls.py:232` runs four overlapping grasps of the
  same mug through one server and asserts exactly one wins.
- **Descriptions come from the skill docstrings** (`tools.py:81`), so the prose
  an agent reads cannot drift from the class it describes; a skill with no
  docstring fails the build.
- **Geometry `required` keys are an explicit table** (`schemas.py:65`). `Point`,
  `Quaternion` and `Pose` have Python defaults for fields their `from_dict`
  requires on the wire, so field defaults are the wrong source of truth there.
  The table is checked against the real parsers key by key
  (`test_schemas.py:114`), so it cannot quietly go stale.
- **`test_stdio_transport.py` spawns `sys.executable -m robot_mcp`** with
  `PYTHONPATH` from `sys.path` rather than literally shelling out to `pixi run`:
  same entry point and same transport, without nesting a pixi environment
  inside the test run. Runtime is ~1.4 s, so R9.7 needed no escalation.

## Known limits (not blockers)

- Concurrency is serialized, not parallel: a long skill blocks other tool calls.
  Correct for a synchronous seam, and the natural place to revisit if
  cancellation/async execution is ever designed (explicitly out of scope here).
- The server exposes no MCP *resources* or *prompts*, only tools.
- `list_tools` ignores pagination (the catalogue is nine entries).
- The in-process `Client` used by most tests does not validate arguments against
  the published `inputSchema` — neither does the low-level server — so a
  schema-violating call is caught by the seam rather than by the protocol. That
  is why the malformed-argument tests assert on the seam's messages.

## Notes for the manager

- No escalations were needed; rulings R1–R12 were followed. The only judgement
  call beyond them is the `skill`-key rejection described above, which tightens
  R5's construction without changing where validation lives.
- `pixi run test`: **307 tests, 0 errors, 0 failures, 0 skipped; audit passed
  for all 9 packages** (`robot_mcp`: 55 tests). `pixi run build` builds all 8
  packages clean.
