# Context: MCP skill-tool server over the Mock backend (issue #37)

## 0. TL;DR for the implementer

Build a new **read-only consumer** package `src/robot_mcp/` that:
- wraps one `MockBackend` (from `robot_backends`, which wraps `robot_skills`),
- exposes it as MCP tools over stdio,
- returns the *exact* `SkillResult.to_dict()` / `Observation.to_dict()` dicts
  the skill API already defines — no re-serialization, no prose.

Nothing in `robot_skills` or `robot_backends` needs to change. Both are pure
Python (no ROS import at module load or lazily — enforced by
`robot_backends/test/test_no_ros_runtime.py`), so `robot_mcp` can and must be
too.

**A real, pinned `mcp==2.0.0` dependency tree is already resolved in
`pixi.lock`** (see §5) even though `pixi.toml` does not yet declare it — this
looks odd and is flagged explicitly below; verify before trusting it blindly.

## 1. The skill seam (`robot_skills`) — full detail

All quotes below are from `src/robot_skills/robot_skills/`.

### 1.1 `Skill` base and wire format (`skills.py`)

- `SKILL_KEY = 'skill'` (`skills.py:68`) — every skill's dict form is
  `{'skill': <wire name>, **payload}` (`Skill.to_dict`, `skills.py:124-126`).
- `SKILL_TYPES: Mapping[str, type[Skill]]` (`skills.py:331`) — a
  `MappingProxyType` over an internal registry, keyed by each subclass's
  `name` ClassVar. Concrete skills self-register via
  `__init_subclass__` (`skills.py:96-111`); `_GripperSkill` is an
  unregistered shared base (`register=False`, `skills.py:297`).
- `skill_from_dict(data)` == `Skill.from_dict(data)` (`skills.py:148-150`).
  `Skill.from_dict` (`skills.py:128-145`):
  1. `ensure_mapping` — raises `SerializationError` if `data` isn't a
     `Mapping` or has non-str keys.
  2. Requires the `'skill'` key — `SerializationError` if missing.
  3. Looks up `wire_name` in `SKILL_TYPES`/`_REGISTRY` — `SerializationError`
     listing all known skill names if unknown:
     `f'unknown skill {wire_name!r} (known skills: {known})'`.
  4. Delegates to `target._from_payload(data)` inside `parse_errors(target.__name__)`
     (`serialization.py:165-181`), which additionally translates any
     constructor-level `TypeError`/`ValueError` into `SerializationError`.
  **`skill_from_dict` therefore raises exactly one exception type,
  `robot_skills.SerializationError` (subclass of `ValueError`), for every
  malformed-input case** (missing key, unknown skill, wrong value type, bad
  enum value, violated invariant). This is exactly what acceptance criterion
  3 wants surfaced as a tool error.

### 1.2 The seven concrete skills — field names, types, wire keys

All are `@dataclass(frozen=True)`, all fields validated in `__post_init__`
via `robot_skills.validation` helpers (`as_identifier`, `as_finite_float`,
`as_enum`, `as_optional_enum` — reject wrong types/blank strings/NaN/±inf
with `TypeError`/`ValueError`, translated to `SerializationError` at the
parse boundary).

| wire `skill` name | class | fields (type, default) | `_payload()` keys |
|---|---|---|---|
| `navigate_to` | `NavigateTo` | `location: str` | `{'location': ...}` |
| `move_gripper` | `MoveGripper` | `side: Side`, `pose: Pose` | `{'side': side.value, 'pose': pose.to_dict()}` |
| `grasp` | `Grasp` | `object_id: str`, `side: Side \| None = None` | `{'object_id': ..., 'side': side.value or None}` |
| `place` | `Place` | `pose: Pose`, `side: Side \| None = None` | `{'pose': pose.to_dict(), 'side': side.value or None}` |
| `extend_column` | `ExtendColumn` | `height: float` | `{'height': ...}` |
| `open_gripper` | `OpenGripper` (`_GripperSkill`) | `side: Side` | `{'side': side.value}` |
| `close_gripper` | `CloseGripper` (`_GripperSkill`) | `side: Side` | `{'side': side.value}` |

`Side` is `Enum('left', 'right')` (`skills.py:74-78`); wire value is the
lowercase string. `SIDE_ORDER = (Side.LEFT, Side.RIGHT)` is the deterministic
left-first preference order used when `side` is omitted.

`Pose` (`geometry.py`) wire form: `{'position': {'x','y','z'}, 'orientation':
{'x','y','z','w'}}` — see `Point.to_dict`/`Quaternion.to_dict`
(`geometry.py:69-71,111-113`). `orientation` is optional on input (defaults to
identity quaternion) but always present on output.

Full dict for each of the 7 skills is exactly `{'skill': <name>, ...fields
above}` — e.g. `Grasp('mug_1', Side.LEFT).to_dict()` ==
`{'skill': 'grasp', 'object_id': 'mug_1', 'side': 'left'}` (matches
`src/robot_skills/test/golden/v1/Grasp.json`).

**These are the exact wire names the MCP tool arguments must use** (per
acceptance criterion 1: "the SAME wire names the skill's `to_dict` uses").

### 1.3 `Observation` / `SkillResult` (`observation.py`, `result.py`)

- `SCHEMA_VERSION = 1`, `SCHEMA_VERSION_KEY = 'schema_version'`
  (`serialization.py:114-117`, D18). Stamped into **every**
  `Observation.to_dict()` and `SkillResult.to_dict()` output, including the
  `Observation` nested inside a `SkillResult` — two independent stamps at two
  depths is intentional (`serialization.py:60-65`).
- `Observation.to_dict()` (`observation.py:406-419`):
  ```
  {
    'schema_version': 1,
    'robot': RobotState.to_dict(),   # pose, location, column_height, grippers[]
    'objects': [SceneObject.to_dict(), ...],
    'known_locations': [str, ...],
  }
  ```
  `RobotState.to_dict()` (`observation.py:283-290`):
  `{'pose': {...}, 'location': str|None, 'column_height': float, 'grippers': [GripperObservation.to_dict(), ...]}`.
  `GripperObservation.to_dict()` (`observation.py:199-207`):
  `{'side','state','pose','held_object_id','grasped'}`.
  `SceneObject.to_dict()` (`observation.py:99-107`):
  `{'object_id','label','pose','graspable','held_by'}`.
- `SkillResult.to_dict()` (`result.py:228-242`):
  ```
  {
    'schema_version': 1,
    'skill': <Skill.to_dict()>,
    'status': 'ok' | 'failed',
    'reason': str | None,
    'code': <FailureCode.value> | None,
    'observation': <Observation.to_dict()>,
  }
  ```
  Invariant (`result.py:173-179`): `status == 'failed'` ⇔ both `code` and
  non-empty `reason` are set; `status == 'ok'` never carries a `code` (but may
  carry an informational `reason`, e.g. `"already at 'kitchen'"`).
- `FailureCode` values (`result.py:91-100`): `unknown_location`,
  `unknown_object`, `not_graspable`, `object_already_held`,
  `gripper_occupied`, `gripper_empty`, `out_of_reach`, `out_of_range`,
  `unsupported_skill`, `rejected`. `BACKEND_REFUSAL_CODES` (all except
  `rejected`) vs `SAFETY_EVENT_CODES` (`rejected` only) — D17. `MockBackend`
  only ever produces backend-refusal codes (never `rejected`), tested in
  `robot_backends/test/test_mock_failures.py::test_every_mock_refusal_is_owned_by_the_backend_not_the_safety_layer`.

Real example (`src/robot_skills/test/golden/v1/SkillResult.json`, quoted in
full) shows a failed `Grasp('bowl_1', Side.RIGHT)` refused
`out_of_reach` — this is the literal shape a tool must return for a refusal,
byte for byte down to key ordering being irrelevant (dict equality, not
string equality, is what the tests should assert).

### 1.4 `MockBackend` (`robot_backends/robot_backends/mock_backend.py`)

- `RobotBackend` ABC (`robot_backends/robot_backends/interface.py:29-54`):
  `reset() -> Observation`, `get_observation() -> Observation`,
  `execute(skill: Skill) -> SkillResult`. **Total**: a legal `Skill` never
  raises from `execute`; only passing a non-`Skill` raises `TypeError`
  (`mock_backend.py:177-179`) — a programmer error, not something an MCP tool
  should ever trigger if it constructs `Skill` objects itself.
- `MockBackend(world: MockWorld | None = None)` — defaults to
  `default_world()` (the "demo apartment", `mock_world.py:173-204`).
  Constructor calls `self.reset()` (`mock_backend.py:125`).
- `reset(self) -> Observation` (`mock_backend.py:134-151`) — restores seed
  state and returns `get_observation()`. **This is the exact method to call
  for the `reset` MCP tool** (acceptance criterion 1).
- Default world (`mock_world.py:186-204`): locations `charger` (start),
  `kitchen`, `table`, `living_room`; objects `mug_1` (graspable, kitchen
  counter), `plate_1` (graspable), `bowl_1` (graspable), `counter_1`
  (**not** graspable — good "refused" test case, `NOT_GRASPABLE`), `book_1`
  (graspable, at `table`), `sofa_1` (not graspable). **Use `'mug_1'` for a
  succeeding grasp test and any nonexistent id (e.g. `'ghost_1'`) or
  `'counter_1'`/`'mars'` for refusal tests** — mirrors
  `robot_backends/test/test_mock_failures.py`.
- Refusal → code mapping used by tests (`mock_backend.py` handlers): unknown
  location → `UNKNOWN_LOCATION`; unknown object → `UNKNOWN_OBJECT`;
  ungraspable → `NOT_GRASPABLE`; already held → `OBJECT_ALREADY_HELD`;
  gripper busy → `GRIPPER_OCCUPIED`; nothing to place → `GRIPPER_EMPTY`;
  too far → `OUT_OF_REACH`; column out of travel → `OUT_OF_RANGE`.
- **No `rclpy`/ROS import anywhere in `robot_skills` or `robot_backends`**,
  enforced by `robot_backends/test/test_no_ros_runtime.py` (AST-walks every
  `.py` file, plus a clean-subprocess probe). `robot_mcp` must hold the same
  property and should get an equivalent test (see §3, §6).

## 2. Worked example (derived from code, not guessed)

```python
from robot_backends import MockBackend
from robot_skills import Grasp, NavigateTo, Side

backend = MockBackend()
backend.get_observation().to_dict()
# {'schema_version': 1, 'robot': {'pose': {...charger pose...},
#  'location': 'charger', 'column_height': 0.3, 'grippers': [...]},
#  'objects': [...6 objects, sorted by object_id...],
#  'known_locations': ['charger', 'kitchen', 'living_room', 'table']}

backend.execute(NavigateTo('kitchen'))          # -> SkillResult, status 'ok'
backend.execute(Grasp('mug_1')).to_dict()
# {'schema_version': 1,
#  'skill': {'skill': 'grasp', 'object_id': 'mug_1', 'side': None},
#  'status': 'ok', 'reason': None, 'code': None,
#  'observation': {...mug_1 now held_by 'left', gripper 'left' state 'closed', grasped True...}}

backend.execute(Grasp('ghost_1')).to_dict()
# {'schema_version': 1,
#  'skill': {'skill': 'grasp', 'object_id': 'ghost_1', 'side': None},
#  'status': 'failed', 'reason': "no object 'ghost_1' in the scene; perceived objects: ...",
#  'code': 'unknown_object',
#  'observation': {...unchanged from before the call...}}
```
(Field values for `SkillResult`/`Observation` confirmed against
`src/robot_skills/test/golden/v1/SkillResult.json` and `Grasp.json`, and
against `robot_backends/test/test_mock_failures.py::test_grasp_missing_object`.)

Malformed argument, e.g. `skill_from_dict({'skill': 'grasp'})` (missing
`object_id`) → raises `robot_skills.SerializationError:
"Grasp: missing required key(s): object_id"`. `skill_from_dict({'skill':
'grasp', 'object_id': 'mug_1', 'side': 'up'})` → `SerializationError:
"Grasp.side: 'up' is not a valid Side (allowed: left, right)"`.

## 3. Existing ament_python package conventions to mirror

Modeled on `src/robot_skills/` end to end (`src/robot_backends/` is the same
shape, plus a `depend robot_skills`):

- `package.xml` — format 3, `<buildtool_depend>ament_python</buildtool_depend>`,
  `<maintainer email="hejaca00@gmail.com">Jaime</maintainer>`, `<license>MIT</license>`,
  `test_depend`s on `ament_copyright`, `ament_flake8`, `ament_pep257`,
  `python3-pytest`, `<export><build_type>ament_python</build_type></export>`.
  **`robot_mcp` must `<depend>robot_skills</depend>` and
  `<depend>robot_backends</depend>`.** Whether it also needs
  `<depend>rclpy</depend>` is an open question (§7) — `robot_skills` and
  `robot_backends` both declare it "reserved for later" even though nothing
  in their Python imports it; `robot_mcp` genuinely must not import it
  per the brief ("no ROS graph required to run or test it").
- `setup.py` — `find_packages(exclude=['test'])`, `data_files` registering
  `resource/<pkg>` + `package.xml`, `install_requires=['setuptools']`,
  `zip_safe=True`, same maintainer/license, `extras_require={'test': ['pytest']}`,
  `entry_points={'console_scripts': [...]}` (currently `[]` in every existing
  package — `robot_mcp` is the first package that plausibly needs a real
  entry point, for the stdio server executable; see §7).
- `setup.cfg` — just the two `script_dir`/`install_scripts` lines pointed at
  `$base/lib/<pkg>`.
- `pytest.ini` — `addopts = -p no:launch_testing -p no:launch_ros` (RoboStack's
  `launch_testing`/`launch_ros` pytest plugins are incompatible with pytest
  ≥ 8 and abort collection if loaded — see the comment block in
  `robot_skills/pytest.ini`), `testpaths = test`. **Copy verbatim.**
- `resource/<pkg>` — an empty marker file (ament package index).
- `test/` — every package has `test_flake8.py`, `test_copyright.py`,
  `test_pep257.py` (ament linter wrappers, copy verbatim — same three files,
  same content, just import from the current package's linter test_depends;
  identical across all 7 existing packages). Real tests live in additional
  `test_*.py` files; shared fixtures/helpers go in a plain module (not
  `conftest.py`) imported directly, e.g. `mock_backend_fixtures.py` — kept
  out of `conftest.py` "so test modules can import them directly without
  relying on a module name every package in the workspace shares"
  (`robot_backends/test/mock_backend_fixtures.py:8-11`). `conftest.py` itself
  holds only pytest fixtures (`backend`, `world` in `robot_backends`).
- `README.md` — short, code-example-first, states pure-Python/no-ROS-graph
  status explicitly (see `robot_skills/README.md:8`,
  `robot_backends/README.md`). **`robot_mcp/README.md` must state the exact
  one-line command per acceptance criterion 6.**

## 4. `scripts/check_test_integrity.py` — what a new package must do

Read in full (`/home/sisyphus/worktrees/.../scripts/check_test_integrity.py`).
Key mechanics:

- `discover_packages()` finds every `package.xml` under `src/` **that git
  tracks** (`_git_tracked_manifests`, using `git ls-files -z`) — an untracked
  package.xml is treated as vendored/unowned and skipped, but once
  `package.xml` is committed (even just `git add`ed and part of this
  feature's commits) it is "expected" and `pixi run test` fails loudly if it
  produces no result.
  **Practically: `robot_mcp/package.xml` must be `git add`ed for the package
  to be audited at all — and once added, it must produce real, non-zero,
  non-all-skipped test results or the whole `pixi run test` run fails.**
- A package "passes" only if colcon produces a **fresh** (this-run) JUnit XML
  with `tests > 0` and at least one non-skipped test case
  (`audit_package`, `_STATUS_ZERO_TESTS` / `_STATUS_ALL_SKIPPED` /
  `_STATUS_NO_RESULT` / `_STATUS_STALE` are all failures). Colcon's own
  `test-result` step considers an empty result "success"; this guard exists
  specifically to catch that — **a zero-test `robot_mcp` package is a hard
  failure of `pixi run test`**, matching acceptance criterion 5's explicit
  callout.
- No special config needed beyond the standard `pytest.ini` (colcon's
  built-in `ament_python`/pytest test step auto-discovers `test/test_*.py`).

## 5. The MCP SDK — what's actually available (read from installed source)

**Empirically-observed** (files physically present in this environment,
`/home/sisyphus/.cache/rattler/cache/uv-cache/archive-v0/MTCeiNSTtXTvIduR/mcp/**`
and `.../U1tm0cdb1UnaTYTl/mcp_types/**` — extracted wheel contents, read
directly):

### 5.1 IMPORTANT — dependency state is inconsistent right now

- `pixi.toml` (`/home/sisyphus/worktrees/.../pixi.toml`) has **no
  `[pypi-dependencies]` table at all** — only conda `[dependencies]`
  (`ros-jazzy-desktop`, `colcon-common-extensions`, `python`).
- `pixi.lock`, however, **already contains a fully resolved pypi dependency
  tree** for `mcp==2.0.0` (24 pypi packages total: `mcp`, `mcp-types`,
  `httpx2`, `httpcore2`, `anyio`, `pydantic`, `pydantic-core`, `starlette`,
  `uvicorn`, `sse-starlette`, `jsonschema`, `pyjwt`, `opentelemetry-api`,
  `python-multipart`, `click`, etc. — `pixi.lock:26758-27003` approx).
  This is a **lock/manifest mismatch**: a lock file with pypi entries that no
  `pixi.toml` dependency requests is not something `pixi install` normally
  produces on its own.
- I verified this is *not* a fabrication/typosquat: the local `uv` simple-index
  cache (`/home/sisyphus/.cache/rattler/cache/uv-cache/simple-v21/pypi/mcp.rkyv`)
  is a genuine cached response from `https://pypi.org/simple/mcp/` (real ETag,
  real URL) and lists real released versions `mcp-0.9.1` … `mcp-1.29.0` …
  `mcp-2.0.0a1..rc1..2.0.0`, and the `mcp-2.0.0-py3-none-any.whl` sha256 in
  that index (`1cb4c75d2d2c7b8c1d756355e5d82a39f2822cc7f13e22a2051d7ca3592349d6`)
  matches the hash pinned in `pixi.lock` exactly. Likewise `httpx2`,
  `httpcore2`, `mcp-types` all have genuine cached PyPI index responses. **So:
  as of the "today" of this environment (2026-08-10), the real MCP Python SDK
  has reached 2.0.0 and depends on real packages named `httpx2`/`httpcore2`
  (apparent successors to `httpx`/`httpcore`) and a split-out `mcp-types`
  package — this is beyond my training knowledge and is confirmed only by
  this local evidence, not by memory.**
- The actual **extracted wheel source** for all of these is sitting in
  `/home/sisyphus/.cache/rattler/cache/uv-cache/archive-v0/<hash>/...` (e.g.
  `mcp` at `.../MTCeiNSTtXTvIduR/mcp/`, `mcp_types` at
  `.../U1tm0cdb1UnaTYTl/mcp_types/`) — this is real, readable Python source,
  not metadata-only. **Everything in the rest of §5 is read directly from
  these files.**
- **There is no `.pixi/` environment anywhere for this worktree or repo** — no
  `pixi install` has been run here, so nothing is actually importable yet.
  Only wheel *contents* were pre-fetched into the shared rattler/uv cache
  (apparently by whoever staged this exercise, so `pixi install` can succeed
  without live network access once `mcp` is added to `pixi.toml`).
- **Recommended action for the implementer**: add
  `[pypi-dependencies]` / `mcp = "*"` (or a version pin like `"==2.0.0"`) to
  `pixi.toml` per the brief's "touch only the dependency table" instruction,
  then run `pixi install` and confirm it resolves using the existing lock
  (ideally without needing network access, since the cache is pre-warmed) and
  that `import mcp` actually works. **Do not assume this succeeds untested —
  verify it empirically before relying on any API below at runtime.** Flag to
  the manager if `pixi install` wants to touch/regenerate lock entries beyond
  what's already there, since that would touch more than "only the dependency
  table" as the brief instructs.

### 5.2 Real API surface (mcp 2.0.0) — read from source, not memory

This SDK version is a *2.0 rewrite*; it does **not** match the well-known
1.x API (`mcp.server.fastmcp.FastMCP`, `mcp.types`, etc. as literal names) —
those names are gone or moved:

- **No `mcp.server.fastmcp` module.** The FastMCP-equivalent ergonomic server
  is now `mcp.server.mcpserver.MCPServer` (also re-exported as
  `mcp.server.MCPServer`) —
  `.../mcp/server/mcpserver/server.py:147` class `MCPServer(Generic[LifespanResultT])`.
  Constructor: `MCPServer(name: str | None = None, title=..., description=...,
  instructions=..., ..., *, tools=None, resources=None, ...)`.
  `.tool(name=None, title=None, description=None, annotations=None, icons=None,
  meta=None, structured_output=None)` decorator (`server.py:621-689`);
  `.add_tool(fn, name=None, ..., structured_output=None)` (`server.py:570-608`);
  `.call_tool(name, arguments, context=None) -> CallToolResult | InputRequiredResult`
  (`server.py:498-504`) — **callable directly, no transport needed**, useful
  for handler-level tests.
  `.run(transport='stdio')` — synchronous, calls `anyio.run(self.run_stdio_async)`
  (`server.py:387-409`). `run_stdio_async` does
  `async with stdio_server() as (r, w): await self._lowlevel_server.run(r, w, ...)`
  (`server.py:1018-1025`).
- **Low-level server**: `mcp.server.lowlevel.Server` (also `mcp.server.Server`)
  — `.../mcp/server/lowlevel/server.py:128`. Constructor takes `name`, and
  `on_list_tools`, `on_call_tool`, etc. handler callables directly (no
  decorators) — `Server(name, on_list_tools=..., on_call_tool=...)`
  (`server.py:128-233`). `on_call_tool` signature:
  `Callable[[ServerRequestContext, types.CallToolRequestParams], Awaitable[types.CallToolResult | types.InputRequiredResult]]`.
  `Server.run(read_stream, write_stream, init_options, raise_exceptions=False)`
  (`server.py:691-718`) drives one connection to completion.
- **`mcp_types` is now a separate top-level package** (not `mcp.types`,
  though `mcp/__init__.py:63` does `from . import types as types` so
  `mcp.types.Tool` etc. still resolves as a compat shim). Import as
  `import mcp_types as types` (this is the pattern used throughout the SDK's
  own source). `mcp_types.CallToolResult` fields (from
  `.../mcp_types/_v2026_07_28/__init__.py:2687-2731`, `WireModel`/pydantic):
  - `content: list[ContentBlock]` (required — no default seen in the read
    slice; confirm empirically whether `content=[]` is legal when you only
    want structured content)
  - `is_error: bool | None` — wire alias `isError`
  - `structured_content: Any | None` — wire alias **`structuredContent`**
  - `result_type: str` — wire alias `resultType` (required in this protocol
    era; `"complete"` for a normal synchronous result)
  `mcp_types.Tool` fields (`_v2026_07_28/__init__.py:2134-2167`):
  `name`, `title`, `description`, `input_schema` (alias **`inputSchema`**,
  a JSON-schema dict), `output_schema` (alias `outputSchema`), `annotations`,
  `icons`.
- **Stdio transport**: `mcp.server.stdio.stdio_server()` — async context
  manager yielding `(read_stream, write_stream)`
  (`.../mcp/server/stdio.py:161-217`). Raises `RuntimeError` if called twice
  concurrently in one process (fd-claim guard). This is the transport
  acceptance criterion 1 requires.
- **In-memory / loopback testing** — two real options, both confirmed by
  reading source:
  1. **`mcp.client.Client`** (`.../mcp/client/client.py:260-951`), the
     high-level unified client. `Client(server)` where `server` is an
     `MCPServer` or low-level `Server` **instance** connects in-process with
     no streams/JSON-RPC framing at all (`_connect_inproc`,
     `client.py:102-120`, used when `mode="auto"`, the default). Usage:
     ```python
     async with Client(mcp_server) as client:
         result = await client.call_tool("navigate_to", {"location": "kitchen"})
         # result: mcp_types.CallToolResult
     ```
     This is the SDK's own documented example (`client.py:267-283`).
  2. **`mcp.shared.memory.create_client_server_memory_streams()`**
     (`.../mcp/shared/memory.py:15-33`) — lower-level, yields
     `(client_streams, server_streams)` stream pairs for driving
     `ClientSession`/`Server.run` manually. **Note: there is no
     `create_connected_server_and_client_session` helper in this version**
     (that was a 1.x convenience function) — `Client(server)` (option 1)
     is the closer modern equivalent and almost certainly the simpler choice
     for the acceptance tests.
  3. **Simplest of all, and what I'd actually recommend for most of the
     required tests**: since `MCPServer.call_tool(name, arguments)` is a
     plain async method with no transport involved (`server.py:498-504`),
     tests can call it directly without any client/transport machinery,
     `await server.call_tool('grasp', {'object_id': 'mug_1'})`, and inspect
     the returned `CallToolResult`. This is a legitimate reading of
     the brief's "handler-level tests" option in acceptance criterion 5.

### 5.3 Structured output mechanics (read from source)

`.../mcp/server/mcpserver/utilities/func_metadata.py`:
- `FuncMetadata.convert_result()` (`func_metadata.py:110-144`) is what turns
  a Python function's return value into a `CallToolResult` when a tool is
  registered via `MCPServer.tool()`/`add_tool()`. If the function has a
  return-type annotation and `structured_output` is not `False`, the return
  value is validated against a Pydantic model derived from that annotation
  and the *validated, re-dumped* form becomes `CallToolResult.structured_content`
  (`func_metadata.py:140-144`); an **unstructured** `content` list (a
  `TextContent` block with `json.dumps`-like text via `pydantic_core.to_json`)
  is *also* always populated alongside it (`_convert_to_content`,
  `func_metadata.py:543-574`) — there is no built-in way to get
  `structuredContent` with an empty `content` list through this path.
  **This conflicts with the brief's "no prose reformatting"** unless the
  duplicate `TextContent` is considered acceptable padding (it is not a
  *reformatting* of the data, just a redundant JSON-text copy) — flag this
  as an open question (§7).
- For a function annotated `-> dict[str, Any]`, `_try_create_model_and_schema`
  (`func_metadata.py:366-467`) special-cases `dict[str, T]` with `str` keys
  into a Pydantic `RootModel[dict[str, Any]]` (`_create_dict_model`,
  `func_metadata.py:528-540`) rather than wrapping in `{'result': ...}` —
  so a tool function returning `SkillResult.to_dict()` verbatim, annotated
  `-> dict[str, Any]`, should round-trip through `structured_content` without
  a `{'result': ...}` wrapper. **Not yet empirically verified in this
  environment** (no working `.pixi` env to actually run it) — the implementer
  should write a quick smoke test confirming `structured_content ==
  SkillResult.to_dict()` byte for byte (dict equality) before relying on it.
- **Using `mcp.server.lowlevel.Server` directly instead** sidesteps all of
  this: the low-level `on_call_tool` handler can construct
  `CallToolResult(content=[], structured_content=my_dict, result_type='complete')`
  (or `content=[TextContent(...)]` if an empty list turns out to be
  rejected) by hand, giving full control and no redundant text block. Per
  `func_metadata.py:118-122`'s own comment: *"the lowlevel server tool call
  handler provides generic backwards compatibility serialization of
  structured content ... the lowlevel server simply serializes the
  structured output"* — i.e. the low-level path is the more direct match for
  "return the dict, nothing else." This is a real trade-off the manager
  should rule on (§7).

### 5.4 Exception behavior (relevant to acceptance criterion 3)

- In `MCPServer._handle_call_tool` (`server.py:415-424`): an `MCPError` raised
  by a tool handler is re-raised (becomes a JSON-RPC protocol-level error);
  any other `Exception` is caught and turned into
  `CallToolResult(content=[TextContent(type='text', text=str(e))], is_error=True)`
  — a normal (non-crashing) tool result with `isError=True`.
- In `Tool.run()` (`.../mcp/server/mcpserver/tools/base.py:123-181`): any
  exception from the tool function itself (other than `MCPError`) is wrapped
  as `ToolError(f'Error executing tool {self.name}: {e}') from e` and
  propagates up to the handler above, which converts it to the same
  `isError=True` result.
- **Conclusion**: if a tool function calls `skill_from_dict(...)` and lets a
  `SerializationError` propagate uncaught, `MCPServer` will *automatically*
  turn it into a `CallToolResult(isError=True, content=[TextContent(text=str(exc))])`
  — satisfying "tool error, not a crash" with zero extra code, **if** the
  implementer uses the high-level `MCPServer`/`ToolManager` path. If using
  the low-level `Server` instead, the tool's `on_call_tool` handler must catch
  `SerializationError` itself and build the `isError=True` `CallToolResult`
  by hand (the low-level server's default behavior for an uncaught handler
  exception was not read in this pass — check `mcp/server/runner.py` before
  assuming symmetry).
- **A backend refusal is not an exception at all** — `MockBackend.execute()`
  already returns `SkillResult(status='failed', code=..., reason=...)` for
  every legal-but-refused skill (§1.4). A tool that just returns
  `backend.execute(skill).to_dict()` therefore naturally reports refusals as
  a normal (non-error) tool result whose payload has `status: 'failed'` —
  exactly acceptance criterion 3's requirement, with no special-casing
  needed in the tool code.

## 6. Testing conventions in this repo (relevant to async MCP tests)

- Plain **pytest function style** throughout (`def test_...(fixture):`), not
  `unittest.TestCase`. Fixtures via `@pytest.fixture` in `conftest.py`;
  shared non-fixture helpers in a plain module imported by test files
  directly (see `mock_backend_fixtures.py` pattern, §3).
- `pytest.ini` in every package disables `launch_testing`/`launch_ros`
  plugins (RoboStack/pytest-8 incompatibility) — copy verbatim into
  `robot_mcp/pytest.ini`.
- **No `pytest-asyncio` anywhere in `pixi.lock`** (grepped; zero matches).
  **`anyio` is present** (pulled in transitively by `mcp`, pinned at 4.14.2)
  and **ships its own pytest plugin**, auto-registered via a `pytest11`
  entry point (`anyio-4.14.2.dist-info/entry_points.txt`: `anyio =
  anyio.pytest_plugin`) — confirmed present at
  `.../archive-v0/dklX0scFk0Zl4bQ_/anyio/pytest_plugin.py`. **Empirically
  not yet run in this environment**, but this is the standard, well-known
  anyio pattern (**inferred-from-source + general knowledge, not
  execute-verified here**): mark async tests `@pytest.mark.anyio` and add an
  `anyio_backend` fixture returning `"asyncio"` in `robot_mcp/test/conftest.py`.
  No extra pypi/conda dependency needed beyond `mcp` itself, since `anyio`
  comes along transitively — but the implementer should add it to
  `package.xml`/verify it if `robot_mcp` imports `anyio` directly.
- Golden-fixture pattern (`robot_skills/test/golden/v1/*.json` +
  `test_golden_schema.py`) exists in `robot_skills` for schema-drift
  detection; **not necessarily needed in `robot_mcp`** since this package
  only consumes the wire format, it doesn't define it — but comparing
  against `backend.execute(...).to_dict()` directly (as
  `mock_backend_fixtures.assert_refused`/`snapshot` do) is the right pattern
  to reuse for "returned dict equals `backend.execute()` byte for byte."

## 7. Open questions for the manager

1. **High-level `MCPServer` vs. low-level `Server`.** `MCPServer.tool()`
   auto-derives JSON schemas from Python function signatures/return
   annotations and always emits a redundant `TextContent` block alongside
   `structuredContent` (§5.3). The low-level `Server` requires hand-building
   `CallToolResult`/`Tool` objects (including JSON schemas) but gives exact
   control over the returned shape (no redundant text) and lets tool input
   schemas be derived straightforwardly from `SKILL_TYPES`/dataclass fields
   rather than from a Python function signature shaped to match. **Recommendation:
   low-level `Server`**, since acceptance criterion 2 ("no prose
   reformatting") and criterion 4 ("schemas consistent with the skill
   dataclasses... SKILL_TYPES preferred") both point away from the
   function-signature-driven high-level path. But this is a real design
   fork the implementer will hit immediately — worth a manager ruling before
   coding starts, since it changes almost the whole shape of `server.py`.
2. **`pixi.toml`/`pixi.lock` mismatch (§5.1).** Confirm the plan: add
   `mcp` under a new `[pypi-dependencies]` table (brief says this is
   allowed, "touch only the dependency table"), run `pixi install`, verify
   it resolves against the existing lock without materially changing it
   (ideally a no-op relative to what's already resolved), and verify
   `import mcp` actually works before writing any server code. If
   `pixi install` wants to change lock entries beyond what's already
   there (e.g. resolve to a different mcp version because of a channel
   priority interaction with `ros-jazzy-desktop`'s own Python), escalate —
   that would be touching more than "only the dependency table."
3. **`package.xml` `<depend>rclpy</depend>`?** Existing sibling packages
   (`robot_skills`, `robot_backends`) declare it "reserved for later" even
   though it's unused today. `robot_mcp` has no ROS action-server future use
   case implied by the brief (out of scope explicitly excludes ROS wiring) —
   recommend **not** adding it, to keep `test_no_ros_runtime.py`-style
   guarantees meaningful, but this is a two-line decision the manager can
   make quickly.
4. **Console script vs. `python -m` for the "exact one-line command"
   (acceptance criterion 6).** No existing package in this repo has ever
   populated `entry_points={'console_scripts': [...]}` — they're all `[]`
   stubs. Options: (a) `console_scripts` entry point → `ros2 run robot_mcp
   <name>` after `pixi run build`; (b) plain `python -m robot_mcp.server`
   (works without a colcon build, only needs `robot_mcp` on `PYTHONPATH`
   inside `pixi shell`). Given `robot_mcp` explicitly needs no ROS graph,
   (b) is simpler and matches the "pure Python" framing used by
   `robot_skills`/`robot_backends`, but (a) is more idiomatic for this repo's
   ROS 2 package layout and would be the first real precedent. Recommend (b)
   as the primary documented command, optionally also wiring (a).
5. **`content=[]` legality for `CallToolResult`.** `mcp_types.CallToolResult.content`
   is typed `list[ContentBlock]` with no default in the slice read
   (`_v2026_07_28/__init__.py:2696`) — unclear if an empty list is accepted
   by validation or if at least one content block is enforced elsewhere.
   Needs an empirical check once `pixi install` succeeds, before deciding
   whether tools return `content=[]` (pure structured data) or a minimal
   single `TextContent` echo.
6. **`reset` and `get_observation` "tools" naming/collision risk.** These
   are not `Skill` subclasses (no wire `skill` name in `SKILL_TYPES`), so if
   schemas are derived from `SKILL_TYPES` (criterion 4), `reset` and
   `get_observation` need to be registered by hand alongside the 7
   SKILL_TYPES-derived tools — worth being explicit in the design that these
   two are a fixed addendum, not part of any derivation loop.
