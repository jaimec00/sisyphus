# Context: #56 — Ops hardening (close the CI-green-but-deploy-broken class)

Brief: GitHub issue #56 (verbatim in the manager's dispatch). No `src/`
behavior change; scope is tooling + process (`scripts/`, `pixi.toml`,
`.claude/commands/run-feature.md`, and read-only-verification tests for the
new tooling).

## 0. The live bug this issue is about (confirmed still present)

The #55 drift bug is **not fixed today** — it is exactly the terrain task 1/2
need to close, not a hypothetical:

- `src/robot_mcp/robot_mcp/server.py:73` — `from robot_world import
  FileWorldStore` — unconditional import.
- `src/robot_brain/robot_brain/openclaw/openclaw.robot.json:13` — the launch
  command's `PYTHONPATH` lists only `robot_skills:robot_backends:robot_safety:
  robot_mcp` — **no `robot_world`**.
- `src/robot_brain/test/test_openclaw_config.py:42` — `REQUIRED_PACKAGES =
  ('robot_skills', 'robot_backends', 'robot_safety', 'robot_mcp')` — same
  omission, so the in-repo assertion agrees with the broken config and cannot
  catch it.
- `src/robot_mcp/README.md:109-117` — the documented one-liner PYTHONPATH also
  omits `robot_world`; a separate note at README.md:143-144 says "Add
  `robot_world` to the PYTHONPATH list above" rather than the list itself
  being fixed. (Verified via `git show e13c3e6 -- src/robot_mcp/README.md`:
  that commit added the note as a *diff addition after* the existing list, it
  did not edit the list.)
- By contrast `src/robot_mcp/package.xml:10` **does** `<depend>robot_world
  </depend>` — which is exactly why `colcon test`/`pixi run test` stayed green
  through #55: colcon builds every `src/` package via its manifest graph, but
  the hand-rolled launch-string PYTHONPATH is a second, unsynced source of
  truth that colcon never reads.

This means a self-discovering launcher (task 1) fixes the root cause without
needing anyone to also patch the JSON/test list — but see Open Question 2 for
whether that list should be repointed anyway as part of this issue.

## 1. `scripts/check_test_integrity.py` — driver/audit structure

Single file, both the guard (`audit`) and the driver `pixi run test` invokes
(`main`). Read fully; key anchors:

- Docstring `scripts/check_test_integrity.py:8-51` explains the whole design:
  colcon reports success on 0 tests, `colcon test-result` hides that unless
  `--all`, and the ratchet against `scripts/test_baseline.json`.
- `TOOLING_PACKAGE = '_workspace_tooling'` (`:65`) — the pseudo-package name
  under which `scripts/tests/` (this very guard's own suite) is recorded, run,
  and audited exactly like a ROS package.
- `run_tooling_tests(repo_root, build_base)` (`:694-715`) — runs `pytest
  scripts/tests` writing JUnit to `build/_workspace_tooling/pytest.xml`, with
  `-p no:launch_testing -p no:launch_ros` (RoboStack plugin incompatibility
  workaround, same as `src/robot_mcp/pytest.ini`). **This is the natural home
  for new `scripts/` python tests** — add files to `scripts/tests/` and they
  are auto-collected; no registration needed elsewhere.
- `main(argv=None)` (`:803-965`) is the actual driver `pixi run test` runs.
  Order of operations, all unconditional (every stage runs even if an earlier
  one failed — `:920-943`):
  1. `delete_result_files` (`:916-918`)
  2. `colcon test ...` → `rc_test` (`:920-929`)
  3. `run_tooling_tests(...)` → `rc_tooling`, **skipped when `--packages-select`
     narrows the run** (`:931-933`)
  4. `colcon test-result --all --verbose ...` → `rc_result` (`:936-939`)
  5. `audit(...)` → `rc_audit` (`:941-944`)
  6. baseline handling → `rc_baseline` (`:946-951`)
  7. `stages` dict (`:953-959`) folds every rc into one non-zero exit if any
     failed, and prints `FAILED stages: ...` (`:960-963`).
- **Where a new stage would slot in**: the `stages` dict at `:953-959` is the
  single place that decides exit status; a "boot-smoke" stage (task 2) or a
  "pretest provisioning guard" (task 3) implemented *inside this script*
  would add one more `_run(...)` call plus one more dict entry, following the
  exact pattern `rc_test`/`rc_tooling` already use. `scripts/tests/
  test_driver.py` (below) is the test-pattern to imitate for testing that
  addition via `FakeWorkspace`/monkeypatching `guard._run` — see Open
  Question 3 for whether it belongs here at all versus a separate pixi task.
- `_run(cmd, *, cwd=None)` (`:682-691`) is the one subprocess wrapper every
  stage uses; it prints `+ <cmd>` and turns a `FileNotFoundError` into a `127`
  with a "run this through `pixi run test`" hint rather than a traceback —
  the idiom to reuse for any new subprocess-spawning stage.
- `Update the baseline`: `python scripts/check_test_integrity.py
  --update-baseline` (`:29`, `:832-836`); refuses to write from a run that
  isn't green (`_update_baseline`, `:767-800`, blockers check at `:781-788`).

## 2. `scripts/tests/` — the workspace-tooling pytest suite

- `conftest.py` (`:1-16`) — puts `scripts/` on `sys.path` so `import
  check_test_integrity as guard` and bare-name imports (`from test_audit
  import write_result`) work. Nothing else. Any new test file in this
  directory gets this for free.
- `test_driver.py` (305 lines) — tests the **driver half** (`main`), via a
  `FakeWorkspace` fixture (`:31-110`) that monkeypatches `guard._run`,
  `guard.run_tooling_tests`, `guard.delete_result_files` and writes real
  JUnit XML for the audit half to read. Pattern: `workspace.main(*argv)` →
  assert `rc`, `capsys` output, and `workspace.events`/`workspace.commands`
  order. This is the established idiom for testing anything added to
  `main()`'s stage sequence (e.g., a new boot-smoke or pretest-guard stage) —
  add a fake stage, parametrize `STAGES`, and assert its failure alone fails
  the run (mirrors `test_no_single_failing_stage_can_be_swallowed`,
  `:125-143`).
- `test_audit.py` (647 lines) — tests the **audit half** (`audit_package`,
  `parse_xunit`, `find_implementation_modules`, etc.) using hand-built XML
  fixtures (`write_result`) and `write_source_package` to lay out fake
  packages under a `tmp_path`.
- `test_lint.py` (47 lines) — runs `ament_flake8`/`ament_copyright`/
  `ament_pep257` over the whole `scripts/` directory (`SCRIPTS_DIR =
  Path(__file__).resolve().parent.parent`, `:22`). **Any new `.py` file under
  `scripts/` (including `scripts/tests/`) is auto-linted by this — no shell
  scripts** (ament_copyright only inspects Python; confirmed by the
  docstring `:7-13`). A new `scripts/*.py` file must carry the copyright
  header (§10) and be flake8/pep257-clean or this test fails.
- `test_ratchet.py` (542 lines) — tests the per-package baseline/ratchet
  logic, with its own `write_implementation`/`write_skeleton` helpers.
- **Counted by the ratchet itself**: the whole `scripts/tests/` suite is
  counted as package `_workspace_tooling` in `scripts/test_baseline.json:4`
  (currently `111`). Adding tests here **raises** that count on the next
  `--update-baseline` run; it does not need to be pre-declared. Baseline
  entries per package (`scripts/test_baseline.json:3-14`):
  `_workspace_tooling: 111, robot_backends: 74, robot_brain: 48,
  robot_bringup: 0, robot_description: 0, robot_mcp: 82, robot_perception: 0,
  robot_safety: 176, robot_skills: 106, robot_world: 50`. **Any new test
  added anywhere only ever needs `--update-baseline` if tests are *removed or
  moved* (`BASELINE_HELP`, `:103-105`)** — pure addition never trips the
  ratchet (it only fails on a *drop* below the recorded count,
  `audit_package:487-492`).

## 3. `pixi.toml` tasks and `depends-on`

Full `[tasks]` table (`pixi.toml:36-52`):
```
build          = "colcon build --symlink-install"
test           = "python scripts/check_test_integrity.py"
test-audit     = "python scripts/check_test_integrity.py --audit-only"
install-openclaw = "bash scripts/install_openclaw.sh"
openclaw       = { cmd = "node/node_modules/.bin/openclaw", depends-on = ["install-openclaw"] }
```
- The only existing `depends-on` example is `openclaw` → `install-openclaw`
  (`:52`) — pixi runs the dependency task first, unconditionally, every
  invocation (it is a no-op refresh once `node/` exists — comment at
  `DEVELOPMENT.md:47-48`). This is the closest existing precedent for a
  `test` → `pretest-guard`-style dependency the brief's task 3 asks for.
- `test` today is a bare `python scripts/...` string, not a table — adding
  `depends-on` to it requires converting it to the `{ cmd = ..., depends-on =
  [...] }` table form, same shape as `openclaw`.
- **`pixi run test` is invoked nowhere else in the repo** except by humans/
  agents at the shell and by `.claude/agents/test-runner.md:9` (default
  command for the in-loop test-runner subagent). Confirmed via `grep -rn
  "pixi run test\b"` across `*.md/*.yml/*.sh/*.toml` — the only hits are
  README.md, DEVELOPMENT.md, CLAUDE.md, run-merge-eval.md,
  implementer.md/test-runner.md documentation prose, none of which shell out
  to it themselves; **GitHub Actions never runs it** (§8). So a new
  `depends-on` on the `test` task changes behavior only for whoever runs
  `pixi run test` (or `pixi run build`, unaffected) — the laptop test-runner
  subagent and any human.
- `[pypi-dependencies] mcp = ">=2.0.0, <3"` (`:54-55`) is the only PyPI dep;
  everything else is conda/RoboStack.

## 4. The `robot_mcp` launch story today

- `src/robot_mcp/robot_mcp/__main__.py` **already exists** (`:1-16`): `python
  -m robot_mcp` runs `robot_mcp.server.main()`. Docstring: "Kept alongside the
  `robot_mcp_server` console script because an MCP client config points at a
  command line, and this one works from a plain checkout." So there are
  **already two entry points**: `python -m robot_mcp` (no build needed) and
  the `robot_mcp_server` console script (`setup.py:22-24`, needs `pixi run
  build` + sourcing `install/setup.bash`, and ament_python does not put it on
  `PATH` — README.md:170-175 spells this out and says to prefer `python -m`).
- `src/robot_mcp/robot_mcp/server.py:main` (`:407-410`) — parses `--world-
  state`/`--world-seed` (or `$ROBOT_WORLD_STATE`/`$ROBOT_WORLD_SEED`) and
  calls `anyio.run(run_stdio, backend_from_options(...))`. No PYTHONPATH
  logic lives here — the module assumes its dependencies are already
  importable when the interpreter starts (i.e., **discovery has to happen
  before `python -m robot_mcp`/the console script is invoked**, in whatever
  wraps it).
- `src/robot_mcp/README.md:104-141` documents the exact one-liner and MCP
  client JSON (both hand-list the four packages, §0). `README.md:170-175`:
  the console-script path is explicitly "secondary" and the `python -m` form
  is "the one to prefer".
- `src/robot_brain/robot_brain/openclaw/openclaw.robot.json` is a **merge
  fragment**, not a drop-in config (`robot_brain/README.md:1-30`,
  `test_openclaw_config.py:89-95` asserts `set(FRAGMENT) == {'mcp', 'agents',
  'bindings'}`). It is **not the deployed Pi file** — it gets merged by hand
  into `~/.openclaw/openclaw.json` on the Pi (`robot_brain/README.md:100-117`,
  step-by-step, explicitly "do not copy it over the file"). Per the issue's
  out-of-scope list, repointing the **actual Pi config** at whatever this
  issue produces is Sisyphus's job post-merge — **this file is the in-repo
  template/source of truth the Pi config is derived from**, not itself live.
- `src/robot_brain/test/test_openclaw_config.py:313-321` —
  `test_the_launch_command_carries_every_package_the_server_needs` asserts,
  for each name in `REQUIRED_PACKAGES` (`:42`, currently missing
  `robot_world` — §0), that `f'/src/{package}' in command`. This is a
  **hand-maintained list test**, i.e., exactly the class of guard the issue's
  root-cause narrative says cannot see new packages. If task 1's launcher
  replaces the hand-rolled PYTHONPATH construction inside this JSON's launch
  command with a call to the new self-discovering launcher, this test's
  string-membership assertion (`/src/{package}` inside a long `PYTHONPATH=...`
  literal) would need to change shape — see Open Question 2.
- `src/robot_brain/README.md:107-117` (step 3) explicitly documents that a
  human must manually edit **three** places in the fragment when deploying:
  the PYTHONPATH entries, `--manifest-path`, and the `ssh` destination alias.
  A launcher that self-discovers `src/` would still need *some* absolute repo
  root passed in (it cannot know the Pi's checkout path), so this manual step
  does not fully disappear even under task 1 — worth flagging to the
  implementer, not solving here.

## 5. How existing tests spawn a server over stdio (verified idiom)

Two distinct patterns coexist, confirmed empirically:

- **In-process** (`mcp.client.Client`): `src/robot_mcp/test/mcp_fixtures.py:
  49-64` — `Client(build_server(backend, safety))` as an async context
  manager, no transport, no subprocess. Used by most of `test/*.py` and by
  `test_no_ros_runtime.py`'s `PROBE` string (`:29-50`, itself run inside a
  **separate bare subprocess** via `subprocess.run([sys.executable, '-c',
  PROBE], env=clean_environment(), ...)`, `:53-66`).
- **Real subprocess + real MCP handshake over stdio** (the pattern task 2's
  boot-smoke needs): `src/robot_mcp/test/test_stdio_transport.py`. Exact API,
  **empirically confirmed** (`pixi run --frozen python -c "import mcp;
  inspect.signature(...)"`, see below) to match what the file imports at
  `:20`: `from mcp import ClientSession, stdio_client, StdioServerParameters`
  — i.e., these three are re-exported at the **top-level `mcp` package**, not
  only under `mcp.client.stdio`. Verified signatures:
  - `StdioServerParameters(*, command: str, args: list[str] = ..., env:
    dict[str, str] | None = None, cwd=None, encoding='utf-8',
    encoding_error_handler='strict')`
  - `stdio_client(server: StdioServerParameters, errlog: TextIO = stderr) ->
    AsyncGenerator[(ReadStream, WriteStream), None]` (an async context
    manager)
  - `ClientSession(read_stream, write_stream)` then `await
    session.initialize() -> types.InitializeResult`
  - Usage (`test_stdio_transport.py:34-45,51-56`):
    ```python
    def server_parameters() -> StdioServerParameters:
        return StdioServerParameters(
            command=sys.executable, args=['-m', 'robot_mcp'],
            env=clean_environment())

    async with stdio_client(server_parameters()) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            initialized = await session.initialize()
            assert initialized.server_info.name == 'robot_mcp'
    ```
  - `TRANSPORT_TIMEOUT_SECONDS = 30.0` wraps the whole thing in
    `anyio.fail_after(...)` (`:28-31,51`) — "a server that comes up but never
    answers must fail the run, not stall it".
  - **`mcp_fixtures.clean_environment()`** (`:35-46`) is the reusable helper:
    copies `os.environ`, overwrites `PYTHONPATH` to the **current test
    process's own `sys.path`** (`os.pathsep.join(sys.path)`, `:43` — i.e.
    whatever pytest/colcon already resolved, not a hand list), and drops
    `INHERITED_ENV_TO_DROP = ('ROS_DOMAIN_ID', WORLD_STATE_ENV,
    WORLD_SEED_ENV)` (`:25-32`) so a developer's real world file/ROS graph
    never leaks into a spawned test server.
  - **This is exactly the reusable subprocess-spawn helper** a boot-smoke test
    would want to imitate or import — but note `clean_environment()`
    currently sets `PYTHONPATH` from the *test runner's* `sys.path`, which is
    **not** what task 2 needs to prove (it needs to prove the *launcher's own*
    discovery logic, run as a subprocess of the launcher, not of pytest). A
    boot-smoke test should invoke the launcher script/entry point itself
    (`args=['bash', 'scripts/robot-mcp-launch.sh']` or similar) rather than
    reuse `clean_environment()`'s PYTHONPATH-from-sys.path shortcut, or the
    test would validate pytest's path resolution instead of the launcher's.
- `test_world_state_options.py` and `test_world_state_persists.py`
  (referenced, not fully quoted above) build on `server_parameters()` from
  `test_stdio_transport.py` and `persisted_server()` from
  `test_world_state_persists.py` — both StdioServerParameters factories
  following the same shape.
- The `test_no_ros_runtime.py` pattern (present near-identically in
  `robot_backends` per its own docstring `:11-13`) is: a static AST scan
  (`find_forbidden_imports`, walks `ast.Import`/`ast.ImportFrom`/dynamic
  `import_module` calls) **plus** a clean-subprocess run, because "either one
  alone has a blind spot" (`:11-14`). Useful precedent if the boot-smoke
  guard also wants a belbelt-and-suspenders static check that every `src/
  <pkg>` really is discovered (not just that boot succeeded once).

## 6. `install-openclaw` / `node/` provisioning story

- `scripts/install_openclaw.sh` (`:1-38`): `#!/usr/bin/env bash`, `set -euo
  pipefail`, no copyright header (shell scripts are outside `ament_copyright`
  reach, confirmed §10). Installs into project-local `node/` (gitignored,
  confirmed `.gitignore: node/`) via `npm install --prefix "$prefix"`.
- `pixi.toml:44-52` tasks: `install-openclaw = "bash scripts/
  install_openclaw.sh"`; `openclaw = { cmd = "node/node_modules/.bin/
  openclaw", depends-on = ["install-openclaw"] }`.
- **The exact ~6 failing tests, confirmed empirically by reading the file and
  counting**: `src/robot_brain/test/test_openclaw_validates.py` has 5 `def
  test_*` functions (`grep -c` confirms), one of which
  (`test_the_validator_rejects_a_broken_fragment`, `:204-224`) is
  `@pytest.mark.parametrize('mutation', sorted(REJECTED_MUTATIONS))` over a
  3-entry dict (`:194-201`) → **3 collected test cases**. Total collected:
  `test_the_cli_is_installed_where_the_suite_expects_it` (1) +
  `test_the_shipped_fragment_is_accepted_by_the_installed_openclaw` (1) +
  `test_the_validator_rejects_a_broken_fragment[...]` (3) +
  `test_validating_writes_only_where_the_test_told_it_to` (1) +
  `test_the_child_inherits_no_openclaw_variable_we_did_not_set` (1, does
  **not** call `openclaw_binary()`) = **7 test cases, 6 of which call
  `openclaw_binary()`** (directly or via `validate()` → `openclaw_binary()`,
  or via `cli_version()` → `openclaw_binary()`) and therefore fail identically
  when it is missing. This matches the issue's "~six" exactly.
- **What they check for, precisely**: `openclaw_binary()`
  (`test_openclaw_validates.py:83-87`) does
  `binary = repository_root() / OPENCLAW_RELATIVE_PATH` where
  `OPENCLAW_RELATIVE_PATH = Path('node') / 'node_modules' / '.bin' /
  'openclaw'` (`:58`), and `repository_root()` (`:63-80`) walks up from
  `robot_brain.__file__` for a `pixi.toml` marker. It **already** asserts
  with a specific message: `f'no OpenClaw CLI at {binary}: {INSTALL_HINT}'`
  where `INSTALL_HINT = 'run \`pixi run install-openclaw\` (project-local,
  gitignored)'` (`:60,86`). So the underlying assertion message is *not*
  opaque in isolation — the issue's "opaque" framing is about how it reads
  buried inside ~6 failing pytest tracebacks mixed into a much larger `colcon
  test` + `_workspace_tooling` run, not that the message itself is
  unexplained. A pretest guard (task 3) would front-run this with one clear,
  single failure **before** `colcon test` even starts, rather than relying on
  a reader to find and de-duplicate the same message six times in scrollback.
- **This worktree is currently provisioned**: `node/node_modules/.bin/
  openclaw` exists (symlink to `../openclaw/openclaw.mjs`), confirmed via
  `ls -la`. So these 6 tests pass here today; the un-provisioned case is what
  a **fresh** worktree/clone hits.
- **Bootstrap already does this for the normal feature loop, best-effort**:
  `scripts/start-feature.sh:74-77` runs `pixi install` then `pixi run
  install-openclaw` during worktree bootstrap, but both are soft failures
  (`|| echo '...returned nonzero...'`) — a network hiccup leaves the worktree
  silently unprovisioned and the first `pixi run test` inside the loop hits
  exactly the opaque-failure scenario. **`scripts/start-op.sh` does not run
  `install-openclaw` at all** (confirmed via `grep -n install-openclaw
  scripts/start-op.sh` → no hits) — operational-agent worktrees never
  provision it (though operational scope is `docs/`/`.claude`/`scripts`,
  never `src/`, so they may not need `pixi run test` at all; still a gap if
  one ever runs it). `DEVELOPMENT.md:50-58` already documents this exact
  failure mode in prose ("If `robot_brain` goes red with *'no OpenClaw CLI
  at …'*, the remedy is that one command") — i.e. the human-facing
  documentation already exists; task 3 is about making the **tool** say it
  too, fast, before wasting a full `colcon test` run.

## 7. `docs/design/decisions.md`

- 57 lines, decisions **D1–D23**, sections headed `## YYYY-MM-DD — <title>`,
  each containing one or more `- **Dn — <short title> (closes #N).** <prose>`
  bullets. Append-only; "Reversing a decision = add a new dated entry that
  supersedes the old one (don't edit history)" (`:3`).
- Most recent: `## 2026-08-12 — World state moves out of the code and onto
  the disk` → **D23** (`:49-57`), closing #54, from PR #55 — the same PR this
  issue is a retro of. The retro item that got fixed in the wrong (ephemeral)
  copy first is documented in `e13c3e6`'s own commit message (the "docs: D23
  said 'no file at all'; it reads the shipped seed" fixup commit, found via
  `git log --oneline -- docs/design/decisions.md`).
- **Ops-only PRs do not necessarily add a decision**: PR #51 (`7d0c5a1`, "ops:
  add Node.js 24 to the pixi env...") touched `pixi.toml`/`DEVELOPMENT.md`
  but **did not** touch `decisions.md` (confirmed: `git show 7d0c5a1 --
  docs/design/decisions.md` is empty). This is precedent that pure ops/
  tooling work is not automatically a "durable decision" — see Open Question
  6 on whether #56 itself needs a D24.

## 8. CI — what actually runs on GitHub

- `.github/workflows/guards.yml` is the **only** workflow file in the repo
  (confirmed: `ls .github/workflows/` → one file). Its one job,
  `docs-clean`, does exactly one thing: `git ls-files 'docs/features/*'` must
  be empty, else it fails with "Ephemeral feature docs still present". No
  pixi, no colcon, no Python setup step, nothing else.
- This empirically confirms CLAUDE.md's claim (`CLAUDE.md:75-79`): GitHub CI
  has **no** pixi/RoboStack environment, so `pixi run test` (and the new
  boot-smoke / provisioning guard from this issue) **cannot** run on GitHub
  Actions at all — there is no environment there capable of running Python
  with `mcp`/`anyio`/ROS deps installed.
- **Therefore "the gate" in the issue's tasks 1–2 can only mean the laptop
  `pixi run test` gate**, not GitHub Actions — GitHub Actions has no
  mechanism to run a boot-smoke test even if one existed. Task 2's "gate
  step" must be a `pixi run test`-reachable stage (a pixi task and/or a
  `scripts/tests/` or `src/robot_mcp/test/` pytest collected by `colcon test`
  / `run_tooling_tests`), never a GitHub workflow addition.

## 9. `.claude/commands/run-feature.md` — current structure

78 lines total. Confirmed line numbers for the steps named in the brief:
- Step 6 (red-team dispatch): `:47-50` — **already** says "Prompt it with
  where to look hardest: name your own rulings and judgment calls as
  explicit targets (`"attack R5 — does that merge order let a caller
  override the tool name?"`) with concrete failure hypotheses, not a generic
  'review this.'" This substantially **already implements task 5a's spirit**
  (naming rulings as targets with concrete hypotheses) — see Open Question 4
  for whether the brief wants stronger/different wording (specifically
  "disprove this empirically" phrasing) or considers this done.
- Step 7 (fix rounds): `:51-53` — "If BLOCK items exist: resume implementer
  to fix. Max 2 red-team↔fix rounds; surviving NOTES → a follow-up comment
  on the issue..." **Nothing here about a scoped second red-team pass on the
  fix commit itself** (task 5b) — this is genuinely new.
- Step 9 (green → open PR, "ready" signal): `:56-60` — "When green against
  current main: open a squash-merge PR, ensure the full local suite passes,
  and report 'ready' to Sisyphus..." **No mention of re-reading
  `decisions.md`** (task 4) — genuinely new; the natural insertion point is
  immediately before or as part of step 9's "ensure the full local suite
  passes" checklist, since it must happen before the "ready" signal per the
  brief's wording ("pre-'ready' step").
- Step 0 (sync) and steps 1–5, 8, 10 are untouched by the brief's tasks.

**Files that could duplicate/need to stay in sync**, checked explicitly:
- `DEVELOPMENT.md:69-98` (`## The loop, step by step`) is a **narrative
  summary** of the same 10 steps (brief mentions, "Red-team (read-only)
  reviews source + tests vs acceptance criteria → red_team.md (severity
  rubric)" at `:83-84`, "Fix ... (≤2 rounds...)" at `:85-86`) but is
  high-level prose, not the detailed instructions — it does not currently
  repeat the "name each ruling as a target" wording or a decisions.md
  re-read step, so there is nothing there that would *drift out of sync*
  by being missed, but the manager should decide whether DEVELOPMENT.md's
  summary is worth one line too (Open Question 7).
- `.claude/agents/red-team.md` (30 lines) is the **red-team subagent's own
  system prompt** — separate file from `run-feature.md` (which is the
  manager's script for *dispatching* red-team). It does not mention
  "falsifiable target" or per-ruling instructions at all (it's generic:
  "Judge against the acceptance criteria and the CLAUDE.md architectural
  invariants"). The brief's task 5a/5b concern **how the manager prompts**
  red-team (per `run-feature.md` step 6, which is dispatch-time context the
  manager supplies), not the subagent's own baked-in instructions — so
  `red-team.md` itself is very likely out of scope, but flag this distinction
  for the manager since both files use the word "red-team".
- `.claude/commands/run-op.md:15` mentions `decisions.md` in a different
  context (operational agents should not touch it "unless the brief
  explicitly instructs it") — unrelated to task 4, no sync issue.
- No other file in `.claude/` mentions `decisions.md` or duplicates the
  red-team-prompting instructions (confirmed via `grep -rn decisions.md
  .claude/`).

## 10. Repo conventions for `scripts/` files

- **Python** (`scripts/check_test_integrity.py:1-7`, every file under
  `scripts/tests/`): shebang only on the directly-executable driver
  (`#!/usr/bin/env python3`), then the MIT copyright header verbatim:
  ```python
  # Copyright (c) 2026 Jaime C.
  #
  # Use of this source code is governed by an MIT-style
  # license that can be found in the LICENSE file or at
  # https://opensource.org/licenses/MIT.
  ```
  then a module docstring, then imports (stdlib first, third-party/local
  after, alphabetized within each group — matches `ament_flake8`/`isort`-ish
  convention visible throughout). Enforced automatically by
  `scripts/tests/test_lint.py` (flake8 + copyright + pep257 over all of
  `scripts/`, including any new file placed there).
- **Shell** (`scripts/install_openclaw.sh`, `scripts/start-feature.sh`,
  `scripts/pi/dispatch.sh`, `scripts/pi/watch-run.sh`, `scripts/start-op.sh`):
  `#!/usr/bin/env bash` then a `# name.sh — one-line purpose` comment block
  explaining what/why/usage/env vars, **no copyright header** (not enforced —
  `ament_copyright` only inspects `.py`, confirmed by `scripts/tests/
  test_lint.py:7-13`'s own docstring), then `set -euo pipefail` (or `-uo
  pipefail` when a script must survive an expected non-zero, e.g. `watch-
  run.sh:14`). Error idiom: a small `die() { echo "prefix: $*" >&2; exit 1;
  }` helper (`start-feature.sh:26`) used throughout for fatal usage errors,
  with the tool's own name as the prefix.

## Open questions for the manager

1. **Launcher form**: `scripts/robot-mcp-launch.sh` (shell), a Python
   console-script entry (e.g. `robot_mcp_launch = robot_mcp....:main` added
   to `setup.py:22-24`, exec'ing `python -m robot_mcp` after computing
   PYTHONPATH), or both (a thin shell wrapper that just execs `pixi run
   --frozen python -m <new console entry>`)? Evidence: `python -m robot_mcp`
   already works from a plain checkout with no colcon build
   (`__main__.py` docstring), and the README's documented command already
   goes through `pixi run --frozen ... python -m robot_mcp` — a shell
   launcher slots in *before* that invocation (computing PYTHONPATH by
   listing `src/*` dirs with a `package.xml`, mirroring
   `check_test_integrity.py`'s own `find_manifests`/`discover_packages`
   logic at `:171-261`, which already knows how to enumerate every `src/
   <pkg>`); a pure-Python entry point would need to run *after* Python
   startup, i.e. it cannot fix its own interpreter's import path before
   `import robot_mcp` unless it re-execs itself with an augmented
   `PYTHONPATH`/`sys.path`, or mutates `sys.path` before importing
   `robot_mcp.server` (possible, since `__main__.py` currently does the
   import at module load — a launcher could be a *new* module that computes
   discovery, edits `sys.path`, then imports and calls `server.main()`).

2. **Repoint `openclaw.robot.json` + `test_openclaw_config.py`?** The brief's
   acceptance criteria say "No `src/` behavior change; the seam ... is
   untouched" but `openclaw.robot.json` and its test live under `src/
   robot_brain/`. Two readings: (a) editing the launch-command *string*
   inside a config JSON and updating the test that asserts its shape is not
   a "behavior change" to the seam (RobotBackend/Observation/SkillResult/
   SCHEMA_VERSION are untouched either way) — it's config/process, matching
   the issue's "Root-cause class" framing; or (b) any edit inside `src/` is
   out of scope for this ops issue and the drift-prone hand list in
   `openclaw.robot.json`/`test_openclaw_config.py:42` stays exactly as
   broken as it is today (§0), to be fixed by whoever next touches
   `robot_brain`/deploys — leaving this issue's launcher only protect
   *future* packages via the new launcher, not retroactively fix the
   `robot_world` omission already live in `openclaw.robot.json`. Evidence:
   the issue explicitly says "The Pi-side `openclaw.json` launch string is
   repointed at this launcher by Sisyphus post-merge; that config lives
   outside the repo and is not part of this PR" — implying the **in-repo**
   template (`openclaw.robot.json`) doesn't need to change either, since the
   whole point of the launcher is that Sisyphus's Pi-side repoint (using the
   new launcher command in place of the hand PYTHONPATH string) happens
   *after* merge, outside this PR. This leans toward (b), but it leaves the
   in-repo `REQUIRED_PACKAGES` list at `test_openclaw_config.py:42` and the
   json's `PYTHONPATH` string both still silently missing `robot_world` on
   `main` after this PR merges, until Sisyphus's post-merge repoint uses the
   launcher. Needs an explicit ruling either way.

3. **Where the boot-smoke lives**: a pixi task (e.g. `boot-smoke = "python
   -m pytest src/robot_mcp/test/test_boot_smoke.py"` or similar, run as its
   own task), a `scripts/tests/` pytest (counted under
   `_workspace_tooling`, run by `run_tooling_tests`, §1/§2), a `src/
   robot_mcp/test/` pytest (counted under `robot_mcp`'s own baseline entry,
   run by `colcon test`, follows the `test_stdio_transport.py` idiom
   directly, §5), or a new stage wired into
   `check_test_integrity.py`'s `main()` stages dict (§1, "where a new stage
   would slot in"). Evidence for `src/robot_mcp/test/`: it is the package
   that owns the server and already has the exact spawn-a-subprocess-and-
   handshake idiom (`test_stdio_transport.py`) to imitate, and adding a test
   there is "free" under the ratchet (§2, additions never trip it) — but the
   boot-smoke needs to launch through the **new launcher** specifically (not
   `python -m robot_mcp` directly, since that already works standalone; the
   launcher is what needs proving), so it also needs to know the launcher's
   path/invocation, which is more naturally a `scripts/`-level concern.
   Acceptance criterion 1 phrasing ("Add a gate step ... that runs the
   actual task-1 launcher") reads most literally as a `scripts/tests/` test
   or a pixi task invoking the launcher script directly, but is not fully
   determined by the brief.

4. **Task 5a's wording**: is the existing `run-feature.md:47-50` text
   ("name your own rulings and judgment calls as explicit targets ...
   concrete failure hypotheses, not a generic 'review this'") already
   sufficient, or does the brief want the more specific imperative framing
   "hand the red-team the specific claim and instruct 'disprove this
   empirically'" added/substituted verbatim? The existing text is close in
   spirit but does not use the word "empirically" or frame it as
   disprove-this. Given #55's retro is the source of this task, and #55's
   round-1 red-team already used something close to this pattern
   (`run-feature.md` already reflects an earlier iteration), this may be a
   request to *sharpen* existing wording rather than add a wholly new
   instruction — worth an explicit ruling on exactly what text changes.

5. **How to "prove it bites" (acceptance criterion 1) as an automated,
   non-flaky test**: dropping a required `src/` package from discovery must
   make the boot-smoke fail, and this must be provable without breaking a
   normal run. Candidate approaches (not evaluated for feasibility, just
   surfaced): (a) a unit test of the launcher's *discovery function* in
   isolation (e.g. point it at a temp directory tree with N packages, assert
   the computed PYTHONPATH lists all N, then remove one and assert it's
   gone) — fast, hermetic, but does not prove the *server* actually fails to
   boot without it; (b) an integration test that runs the real launcher
   against a temp copy/symlink-subset of `src/` with one package's
   `package.xml` or directory hidden, and asserts the spawned server exits
   non-zero / fails the MCP handshake — closer to the literal acceptance
   criterion but slower and needs care not to mutate the real `src/` tree
   during a normal `pixi run test` run. `check_test_integrity.py`'s own
   `find_manifests`/`discover_packages` (§1) is a working, tested example of
   "enumerate `src/<pkg>` from a given `--source-dir`" that already supports
   being pointed at an arbitrary directory for exactly this kind of test
   (see `scripts/tests/test_driver.py`'s `FakeWorkspace` which builds a
   fake `src/`/`build/` under `tmp_path`).

6. **Does #56 need a new decision entry (D24)?** Evidence both ways: PR #51
   (ops-only, Node.js env addition) did **not** add a decision (§7) — direct
   precedent for "pure ops/tooling PRs don't get a D-number". Against that,
   D20 (`decisions.md:34-36`) *did* get a decision for what reads as
   comparably "ops plumbing" (push-primary run-end notification) — so the
   line isn't "ops PRs never get a D", it's closer to "durable architectural
   commitments get one, mechanical environment/tooling additions don't".
   #56's tasks are self-discovering launcher (a mechanism, arguably durable
   enough to be D-worthy the way D20 was for notification plumbing), a gate
   step, a provisioning guard, and two run-feature.md process edits — closer
   in kind to D20 than to #51's "add a dependency to pixi.toml". No
   `PROJECT.md`/architecture change either way. Needs an explicit ruling;
   if yes, task 4 itself (re-reading `decisions.md` against the final diff)
   would then need to catch its own issue's entry, which is a nice
   dogfooding test of task 4 if it's added last.

7. **Should `DEVELOPMENT.md`'s narrative summary (§9) also gain a line**
   for the decisions-re-read step and/or the red-team heuristic, or is
   updating `run-feature.md` alone (per the brief's literal wording, which
   names only that file) sufficient? `DEVELOPMENT.md` is prose-level and
   currently omits several `run-feature.md` details already (e.g. it doesn't
   mention the manager naming rulings as red-team targets at all), so
   leaving it as-is would be consistent with its existing level of detail —
   but it does explicitly enumerate the 10 steps as a numbered list overlapping
   run-feature.md's, so a future reader skimming only `DEVELOPMENT.md` would
   not learn about the new pre-ready step.

8. **Provisioning guard scope**: should it *only* detect the missing
   `node/node_modules/.bin/openclaw` binary (the exact condition
   `test_openclaw_validates.py` already checks, §6), or also cover other
   un-provisioned conditions the brief did not name (`.pixi/` env missing —
   though `pixi run test` cannot even start without that, so it's likely
   moot; `build/` missing — `check_test_integrity.py` already handles a
   missing `build/` gracefully per `audit_package:430-436`, reporting
   `no-result` per package rather than crashing)? The brief's task 3 text
   scopes it explicitly to "detects the missing install" (singular,
   install-openclaw), suggesting narrow scope, but worth an explicit ruling
   since "fresh-worktree provisioning guard" as a title reads broader than
   "openclaw-only guard".

9. **Hard fail or skip for the pretest guard?** `test_openclaw_validates.py`
   already has a strong precedent *against* skipping (`:28-33`, "No skip. ...
   A drift guard that quietly turns itself off ... is the same lie as the
   docstring #52 deleted"). A pretest guard that merely prints a friendlier
   message before the same 6 tests still fail is consistent with that
   precedent (loud, not silent); a guard that *skips* the OpenClaw tests
   after printing the remediation would contradict it. Given the existing
   precedent, hard-fail-fast-with-a-clear-message (not skip) seems the
   consistent choice, but should be an explicit ruling rather than assumed.
