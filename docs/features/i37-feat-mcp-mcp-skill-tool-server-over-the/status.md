# status — i37 feat(mcp): MCP skill-tool server over the Mock backend

- **Issue:** #37
- **Branch:** `feat/i37-feat-mcp-mcp-skill-tool-server-over-the`
- **Phase:** manager rulings recorded → implement
- **Round:** —
- **Blockers:** none

## Log
- Synced with `origin/main` (`c926084`); worktree clean.
- Read brief from issue #37 (body present; criteria 1–6; owned paths
  `src/robot_mcp/**` + `pixi.toml` dependency table only).
- context-explorer → `context.md` (thorough; a few of its claims about the
  environment were raced by the manager's concurrent `pixi add` — corrected
  below under R2/R5).
- **Manager: added the MCP SDK dependency and verified the environment**
  (see R2). `pixi add --pypi "mcp>=2.0.0,<3"` → `mcp==2.0.0` installed;
  `pixi.lock` diff is **purely additive** (1174 insertions, 0 deletions).
- Manager rulings R1–R12 recorded (below) — design locked before implementation.

---

# Manager rulings (locked before implementation)

These are binding. An implementer who thinks one is wrong escalates to the
manager in-process rather than silently deviating.

## R1 — Package name: `robot_mcp`
Confirmed as proposed in the brief. ament_python package at `src/robot_mcp/`,
mirroring `src/robot_skills/` boilerplate exactly (`setup.py`, `setup.cfg`,
`package.xml`, `pytest.ini`, `resource/robot_mcp`, `test/`, `README.md`).

## R2 — Dependency: `mcp>=2.0.0,<3` via `[pypi-dependencies]` — **already done**
The manager performed this step so the implementer starts from a working env:

```toml
[pypi-dependencies]
mcp = ">=2.0.0, <3"
```

Verified empirically in this worktree:
- `mcp 2.0.0` + `mcp_types 2.0.0` are installed in `.pixi/envs/default`.
- `pixi.lock` diff is **additive only** — no existing entry changed or removed.
  (`context.md` §5.1 describes a "lock/manifest mismatch"; that was an artifact
  of the explorer reading the tree mid-`pixi add`. There is no mismatch now.)
- `anyio 4.14.2`, `pydantic 2.13.4`, `jsonschema`, `httpx2` came in transitively.

**Implementer: do not re-run `pixi add` and do not otherwise edit `pixi.toml`.**
Use `pixi run --frozen ...` for probes. If anything makes you want to change the
lock beyond what is committed, escalate.

## R3 — Use the **low-level** `mcp.server.lowlevel.Server`, not `MCPServer`
Both were probed empirically against the installed SDK. Ruling: **low-level.**

Rationale — the high-level `MCPServer.tool()` path derives input schemas from
*Python function signatures* (emitting artifacts like
`{'title': 'graspArguments'}`), which is the drift-prone thing criterion 4 asks
us to avoid, and it cannot express `SKILL_TYPES`-driven registration. The
low-level `Server` lets tool registration and schemas be **derived from the
seam itself** (R4) and gives exact control over the returned result shape (R6).

Real API surface, confirmed by running it (not from memory — this SDK is a 2.x
rewrite; `mcp.server.fastmcp` and `mcp.types` as literal modules are gone):

```python
import mcp_types as types                     # types live in a separate package
from mcp.server.lowlevel import Server
Server(name, version="...", on_list_tools=..., on_call_tool=...)   # version is keyword-only
# on_list_tools(ctx, params) -> types.ListToolsResult(tools=[types.Tool(...)])
# on_call_tool(ctx, params)  -> types.CallToolResult(...)   # params.name, params.arguments
from mcp.server.stdio import stdio_server     # async CM -> (read_stream, write_stream)
from mcp.client import Client                 # Client(server_instance) connects IN-PROCESS
```

`types.Tool` uses wire alias `inputSchema`; `types.CallToolResult` uses
`structuredContent` / `isError` (fields `structured_content` / `is_error`).

**Consequence the implementer must handle (empirically confirmed):** the
low-level server does **no** dispatch or argument validation for you.
- An uncaught exception in `on_call_tool` surfaces to the client as a
  protocol-level `MCPError(-32603 Internal server error)` — i.e. a *crash*, which
  criterion 3 forbids. Errors **must** be caught in the handler (R7).
- An unknown tool name is **not** rejected by the SDK; it is dispatched to your
  handler. Handle it explicitly (R7).

## R4 — Derive tools and input schemas from `SKILL_TYPES` (criterion 4's preference)
Do the derivation; do not hand-write 7 schemas. The seam makes this easy — the
full field-type universe across all 7 skills is exactly:
`str`, `float`, `Side` (enum), `Pose` (nested dataclass), `Side | None`.

Requirements:
- Iterate `SKILL_TYPES` (order it deterministically) and emit one `types.Tool`
  per entry, tool name == the wire `skill` name, properties == the skill's
  `dataclasses.fields()` under their **wire key names**.
- Map: `str`→`{"type":"string"}`; `float`→`{"type":"number"}`;
  `Side`→`{"type":"string","enum":["left","right"]}` (derive the enum values
  from `Side`, don't hard-code the list); `Pose`→the nested
  `{"position":{x,y,z},"orientation":{x,y,z,w}}` object schema;
  `Side | None`→the `Side` schema, and **not** in `required`.
- Set `"additionalProperties": false` on every generated object schema.
- **Fail loudly, at import/registration time, on any field type the mapper does
  not know** (raise, don't emit a permissive `{}`). A future skill with a new
  field type must break the build, not silently ship an unusable tool.
- A test must assert the derived tool-name set **equals** `set(SKILL_TYPES)` —
  so adding a skill to `robot_skills` automatically adds a tool.

## R5 — Build every skill through `skill_from_dict`; no parallel validation
The handler constructs the skill as
`skill_from_dict({SKILL_KEY: params.name, **(params.arguments or {})})`.
All validation therefore flows through the frozen seam (invariant 1 / D18), and
`SerializationError` is the single exception type to catch. Verified messages:

| input | result |
|---|---|
| `{'skill':'grasp'}` | `SerializationError: Grasp: missing required key(s): object_id` |
| `{'skill':'grasp','object_id':'mug_1','side':'up'}` | `SerializationError: Grasp.side: 'up' is not a valid Side (allowed: left, right)` |
| `{'skill':'grasp','object_id':'mug_1','bogus':1}` | `SerializationError: Grasp: unknown key(s): bogus (allowed: object_id, side, skill)` |
| `{'skill':'navigate_to','location':5}` | `SerializationError: NavigateTo.location: expected a string, got int` |

The seam already rejects unknown keys, so do **not** add your own arg checking.

## R6 — Result shape: `structuredContent` is the payload; also emit a verbatim JSON text block
Success **and** backend refusal return a normal (non-error) result:

```python
types.CallToolResult(
    content=[types.TextContent(type="text", text=json.dumps(payload))],
    structuredContent=payload,          # payload IS SkillResult.to_dict() / Observation.to_dict()
)
```

`content=[]` was probed and is legal, but ruling is to **include** the text
block: it is a verbatim `json.dumps` of the *same dict* — not prose, not a
reformatting — and it is what the MCP spec recommends for clients that ignore
`structuredContent`, which matters for criterion 6 ("point a real MCP client at
it"). Criterion 2 is satisfied by `structuredContent` being the exact dict.

- `payload` must be the dict **verbatim** — no key filtering, no re-ordering
  logic, no added fields. Tests assert dict equality against
  `backend.execute(...)` / `backend.get_observation()` (R9).
- **Do not** declare `outputSchema` on the tools. The skill layer's
  `schema_version` stamp (D18) is the versioning mechanism; duplicating the
  observation schema here would be exactly the parallel-serialization drift the
  brief forbids. (If red-team wants it, it is a NOTE, not a BLOCK.)

## R7 — Error path (criterion 3)
- **Backend refusal is not an error.** `MockBackend.execute()` returns
  `SkillResult(status='failed', code=..., reason=...)`; return it through the
  normal R6 path with `isError` unset. No special-casing.
- **`SerializationError`** (malformed argument) → catch it and return
  `isError=True` with the seam's own message, plus a machine-readable envelope:
  ```python
  types.CallToolResult(
      content=[types.TextContent(type="text", text=str(exc))],
      structuredContent={"error": "SerializationError", "message": str(exc)},
      isError=True,
  )
  ```
- **Unknown tool name** → same `isError=True` shape, `error` naming the
  condition and the message listing the known tool names. Never raise.
- No other exception type should be able to escape the handler; a catch-all that
  converts an unexpected `Exception` into an `isError=True` result is required
  so the server process never dies on one bad call.

## R8 — Entrypoint: ROS-free console script + `python -m robot_mcp`
Per the brief's recommendation, **not** a ROS node.
- `robot_mcp/server.py` exposes `build_server(backend=None) -> Server` and
  `main() -> None` (`main` runs the stdio transport via `anyio.run`).
- Register `console_scripts` entry point `robot_mcp_server = robot_mcp.server:main`
  in `setup.py` — this is the repo's first real entry point; that is fine and
  intended.
- Also add `robot_mcp/__main__.py` so `python -m robot_mcp` works **without a
  colcon build**, which is what the README one-liner uses (R11).
- `build_server` takes an **injectable backend** (defaulting to `MockBackend()`)
  so tests can inject one and compare against it directly. One backend instance
  per server, per criterion 1.

## R9 — Tests (criterion 5) — required matrix
Deterministic, Mock-only, no ROS graph. Use the **in-process client**
(`async with Client(build_server(backend)) as client:`) as the primary driver —
it is a real MCP client session, so this genuinely exercises the server, not
just the handler function. Async tests use the **anyio** pytest plugin (ships
with `anyio`, already installed; no `pytest-asyncio` in this env): mark
`@pytest.mark.anyio` and add an `anyio_backend` fixture returning `"asyncio"` in
`test/conftest.py`.

Must cover:
1. **The required sequence** — `reset` → `navigate_to` → `grasp` →
   `get_observation`, asserting each returned `structuredContent` equals the
   result of the same call on a **parallel, identically-seeded backend**
   (`backend.execute(...).to_dict()` / `.get_observation().to_dict()`) by dict
   equality. Use `mug_1` for the succeeding grasp (`kitchen` for navigation).
2. **`content[0].text` round-trips**: `json.loads(result.content[0].text) ==
   result.structured_content`.
3. **Refusal path** — `grasp` on a nonexistent id (`ghost_1`) and on a
   non-graspable object (`counter_1`): `isError` falsy, payload
   `status == 'failed'` with the expected `code` (`unknown_object` /
   `not_graspable`), and dict-equal to the backend's own result.
4. **Malformed-argument path** — at least missing-required, bad-enum-value, and
   unknown-key; each returns `isError=True` and a message from the seam.
5. **Unknown tool name** → `isError=True`, no exception.
6. **Tool listing** — `list_tools` names == `set(SKILL_TYPES) | {'get_observation','reset'}`;
   every tool has a non-empty `description` and an object `inputSchema` with
   `additionalProperties: false`; schema properties match the dataclass fields
   (this is the anti-drift test for R4).
7. **One real stdio round trip** — spawn the documented one-liner as a
   subprocess via `mcp.StdioServerParameters` + `mcp.stdio_client`, initialize,
   and make one `call_tool`. This is the only thing that proves criterion 1's
   "stdio transport" and the README command actually work. If it proves flaky or
   slow, **escalate to the manager** — do not silently drop it.
8. **`test_no_ros_runtime.py`** — mirror
   `src/robot_backends/test/test_no_ros_runtime.py` so the ROS-free property is
   enforced, not just asserted in prose.
9. Copy `test_flake8.py`, `test_copyright.py`, `test_pep257.py` verbatim from a
   sibling package.

`robot_mcp/package.xml` **must be `git add`ed** — `scripts/check_test_integrity.py`
only audits git-tracked packages, and once tracked a zero-test package is a hard
failure. `pixi run test` must be green.

## R10 — Concurrency: serialize backend access
The low-level server dispatches handlers in a task group, so two tool calls can
interleave. `MockBackend` is stateful and not reentrant. Guard all
`execute`/`reset`/`get_observation` access with a single `anyio.Lock` held by
the server instance. Three lines; prevents a real interleaving bug.

## R11 — README one-liner (criterion 6)
The README must give a command that **actually works from a clean checkout**,
and the implementer must run it. `ros2 run` is excluded (ROS-free by R8), and
ament_python installs console scripts to `lib/<pkg>`, which is not on `PATH` —
so the canonical documented command is the no-build one:

```
PYTHONPATH=<repo>/src/robot_skills:<repo>/src/robot_backends:<repo>/src/robot_mcp \
  pixi run --frozen python -m robot_mcp
```

Document it with a concrete absolute-path example **and** an MCP client config
JSON snippet (`command` / `args` / `env`). Mention the built alternative
(`pixi run build`, then the `robot_mcp_server` console script from the install
space) as a secondary note. README also states: pure Python, no ROS graph,
Mock-backed, results are `SkillResult.to_dict()` / `Observation.to_dict()` at
`schema_version: 1`, and that cancellation/e-stop is deliberately absent.

## R12 — `package.xml` depends
`<depend>robot_skills</depend>` and `<depend>robot_backends</depend>`.
**No `<depend>rclpy</depend>`** — siblings carry it "reserved for later", but
`robot_mcp` is ROS-free by design (R8) and R9.8 enforces that; declaring an
unused ROS dep would contradict the test.

## Out of scope — reaffirmed
No cancellation / `/stop` / e-stop, no new `FailureCode`, no async execution
model, no chat/Telegram wiring, no Sim/Real backends. **Anything pushing toward
async execution or cancellation is a genuine design fork → escalate to the
manager, who escalates to Sisyphus.** Do not modify `src/robot_skills/**` or
`src/robot_backends/**`.
