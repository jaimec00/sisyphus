# context — i52: fix `openclaw.robot.json` (`agents.list` + sandbox enum)

Issue #52. Read `docs/features/i52-openclaw-config-validates/status.md` first —
it has the execute-verified schema facts (OpenClaw 2026.7.1-2); this file does
not repeat them, only cites them.

## Acceptance criteria (restated from the issue)

1. `src/robot_brain/robot_brain/openclaw/openclaw.robot.json` uses
   `agents.list` (array, `required:["id"]`, `additionalProperties:false` per
   item) instead of `agents.entries` (object keyed by id) — **empirically
   confirmed wrong shape**, `status.md:37-42`.
2. `sandbox.mode` is one of `off|non-main|all`, not `"read-only"`; read-only
   workspace access is the separate field `sandbox.workspaceAccess: "ro"` —
   **empirically confirmed**, `status.md:43-44`.
3. `src/robot_brain/test/test_openclaw_config.py` updated in step (its three
   `agents.entries` assertions, and its docstring's now-false premise — see
   below).
4. A new test shells out to `openclaw config validate` so schema drift is
   caught automatically, not rediscovered by hand next time OpenClaw ships a
   new build.
5. `mcp.servers`, `tools.allow`, `bindings` are already schema-correct per
   `status.md` — do not touch beyond what the reshape forces.

Owned paths: `src/robot_brain/robot_brain/openclaw/openclaw.robot.json`,
`src/robot_brain/test/test_openclaw_config.py`, plus whatever new test file/
module the validate-shellout test needs (open question 5).

## The current (broken) fragment

`src/robot_brain/robot_brain/openclaw/openclaw.robot.json:32-49`:

```json
"agents": {
  "entries": {
    "robot": {
      "default": false,
      "name": "robot",
      "workspace": "~/.openclaw/agents/robot",
      "skills": [],
      "sandbox": { "mode": "read-only" },
      "tools": { "allow": ["mcp__robot__*"] }
    }
  }
}
```

Per `status.md:37-42`, the schema wants `agents.list` = `[{"id": "robot",
...}]`, item `additionalProperties:false`, item property set includes
`id, default, name, workspace, skills, sandbox, tools` (so `default` must move
out of being a sibling — check it's still a legal item property; it is,
per the probe). `id` is `required` and currently **absent** from the entry —
the reshape must add it, not just rename the container.

## Blast radius of `agents.entries` → `agents.list`

Confirmed complete by a whole-repo grep (`grep -rn "entries" --include=*.py
--include=*.md --include=*.json ...`, excluding `node/`). Every hit:

| File:line | What it does |
|---|---|
| `src/robot_brain/robot_brain/openclaw/openclaw.robot.json:33` | the data itself |
| `src/robot_brain/robot_brain/agent.py:41` | docstring comment: `` #: The OpenClaw agent id, the key under ``agents.entries`` and the directory name under ``~/.openclaw/agents/``.  One name, used everywhere.`` — prose only, needs `entries`→`list` wording fix (the id is no longer a dict *key*, it's the `id` field of a list item) |
| `src/robot_brain/README.md:94` | deploy-procedure bullet: `` - `agents.entries.robot` `` (in "merge exactly three keys") |
| `src/robot_brain/README.md:135` | `` `agents.entries.<id>.sandbox.mode`'s value vocabulary `` (in the "fields most likely to differ between builds" list) |
| `src/robot_brain/test/test_openclaw_config.py:78` | `assert set(FRAGMENT['agents']['entries']) == {AGENT_ID}` |
| `src/robot_brain/test/test_openclaw_config.py:80` | `entry = FRAGMENT['agents']['entries'][AGENT_ID]` |
| `src/robot_brain/test/test_openclaw_config.py:89` | `allowed = FRAGMENT['agents']['entries'][AGENT_ID]['tools']['allow']` |

No hits in `AGENTS.md` (the prompt) — grepped separately, empty. No hits in
`docs/design/`. The manager's list (agent.py:41, README:94/135, three test
asserts) is complete; nothing else in the repo reads or documents this shape.

### Where an id-lookup helper belongs

`agents.entries[AGENT_ID]` was an O(1) dict lookup; `agents.list` is an array,
so "the entry for `AGENT_ID`" needs a linear-search helper somewhere. Two
candidates, both grounded in what's already in the file:

- **Test-local**, mirroring the existing `server()` helper
  (`test_openclaw_config.py:46-48`, a small function local to the test file
  that indexes `FRAGMENT['mcp']['servers'][MCP_SERVER_NAME]`). An analogous
  `agent()`/`agent_entry()` in the same test file, doing `next(e for e in
  FRAGMENT['agents']['list'] if e['id'] == AGENT_ID)`, is the closer-to-zero-
  new-surface option and needs no change to `robot_brain/agent.py`.
- **In `agent.py`**, next to `config_fragment()`. `agent.py`'s own docstring
  (`agent.py:7-24`) frames the module as "the smallest amount of Python that
  lets the test suite hold [the assets] to the live skill API" — i.e. it
  currently ships *only* raw loaders (`operating_prompt()`,
  `config_fragment()`), no derived/business-logic accessors. Adding a lookup
  helper here would be new: nothing in `agent.py` today parses *into* the
  fragment's structure, only reads the file.

Given that philosophy and that only the test file currently needs this
lookup, the test-local option (mirroring `server()`) is the smaller, more
consistent change — but this is a judgment call for the implementer/manager,
not settled by anything already decided in the repo.

## How `pixi run test` actually runs this package's tests

`pixi.toml:44`: `test = "python scripts/check_test_integrity.py"`, which runs
`colcon test` then audits the JUnit results (`scripts/check_test_integrity.py`
docstring, lines ~10-50).

**CWD and rootdir — empirically observed** from a real colcon test log in a
sibling worktree (`/home/sisyphus/worktrees/i46-.../log/test_2026-08-11_19-02-43/robot_brain/{command,streams}.log`,
same repo/colcon setup, captured by an actual `colcon test` run, not inferred):

- colcon invokes `python3.12 -m pytest` with **cwd =
  `<worktree>/src/robot_brain`** (the source directory, *not* `build/`).
- pytest reports `rootdir: .../src/robot_brain`, `configfile: pytest.ini`,
  `testpaths: test` — i.e. `src/robot_brain/pytest.ini:15`'s `testpaths =
  test` is honored relative to the source tree.
- `PYTHONPATH` is set to `.../build/robot_brain:<install/*/lib/python3.12/
  site-packages for the other packages>:...` — `build/robot_brain` contains a
  **symlink** `robot_brain -> src/robot_brain/robot_brain` (confirmed by
  listing `build/robot_brain/` in that same worktree: `robot_brain ->
  .../src/robot_brain/robot_brain`), which is the `--symlink-install`
  colcon build (`CLAUDE.md`: `pixi run build` = `colcon build
  --symlink-install`).

**Consequence for `__file__`-based repo-root discovery** (relevant to finding
`node/node_modules/.bin/openclaw`, which lives at the *repo* root, not under
`src/robot_brain`): `robot_brain.__file__` resolves through the
`build/robot_brain/robot_brain` symlink. `os.path.dirname(robot_brain.__file__)`
(the pattern already used in `test_no_ros_runtime.py:110`, to `os.walk` the
package's own files) works fine for walking *within* the package, because
symlink components resolve transparently on every filesystem access. But
walking *up* with `..` is not transparent to plain string tools:
`os.path.normpath`/`pathlib` path-joining with `..` is **lexical** (does not
touch the symlink), while the **kernel** resolves `A/symlink/..` to the parent
of the symlink's *target*, not of `A`. So `dirname(__file__)/../..` computed by
`os.path.realpath()`/`Path.resolve()` (which asks the OS) lands somewhere
different from the same computed by `os.path.normpath` (pure string). Neither
is obviously "right" without deciding which one the implementer wants — this
is exactly the trap CLAUDE.md's `symlink-install` note exists to warn about.
No existing code in this repo currently walks *up* from `__file__`; nothing to
copy. See open question 2.

**Test count / timing — empirically observed** (same colcon log,
`stdout.log`): that run collected **41** items across
`test_copyright(1) + test_flake8(1) + test_no_ros_runtime(3) +
test_openclaw_config(12) + test_pep257(1) + test_prompt_drift(23)`, in
**1.39s** wall time. That run predates this worktree's current state (Aug 11,
a stale sibling worktree) so the exact number won't match today's tree
one-for-one, but the *shape* (CWD, rootdir, timing order of magnitude — low
single-digit seconds) is representative. The current checked-in floor is in
`scripts/test_baseline.json:6`: `"robot_brain": 38`.

**The ratchet is a floor, not an exact count**
(`scripts/check_test_integrity.py:420-428`, `:481-489`): `pixi run test` fails
only if the package's *collected* non-linter test count drops **below** 38.
Adding tests never trips it. `scripts/check_test_integrity.py` docstring
(`:20-29`) and the header baked into `test_baseline.json:2` both say to
**re-cut and commit** the baseline "whenever tests are legitimately added" —
so best practice is to bump `robot_brain` to its new true count with
`python scripts/check_test_integrity.py --update-baseline` after adding the
new test(s), even though a bump isn't required for green.

**Subprocess-under-test is established prior art**, not a first use in this
package. `test_no_ros_runtime.py:79-92` (this package) already does
`subprocess.run([sys.executable, '-c', PROBE], env=..., capture_output=True,
text=True, timeout=120, check=False)` then asserts on `returncode`/`stdout`.
The same pattern (clean-subprocess probe of an external interpreter, `check=
False`, assert on `.returncode`) repeats in `robot_backends/test/
test_no_ros_runtime.py:52`, `robot_mcp/test/test_no_ros_runtime.py:58` and
`robot_safety/test/test_no_ros_runtime.py:69` — this whole family of packages
already has the shape a "shell out to `openclaw` and check the exit code"
test would follow, just against `node`/`openclaw` instead of `python`.

## `test_openclaw_config.py`'s docstring is now false

`src/robot_brain/test/test_openclaw_config.py:7-21`, verbatim:

```
"""What we can honestly check about the OpenClaw config fragment.

**We cannot check that OpenClaw accepts it.**  OpenClaw runs on the Pi, is not
installed on this laptop, and nothing here validates against its schema -- the
field names come from its documentation, and the README tells the operator to
verify them on the Pi with ``openclaw config schema``.  Any test in this file
that implied otherwise would be lying.

What *is* checkable, and worth checking, is internal consistency: it parses, it
declares the server the prompt assumes, the agent it binds is the agent it
configures, the launch command actually starts *our* server with the packages
it now needs (``robot_safety`` is a runtime dependency since the safety gate
landed), the tools it exposes are the tools that exist -- and it carries no
secret.
"""
```

The bolded sentence and "OpenClaw runs on the Pi, is not installed on this
laptop" are **both false now**: PR #51 (`7d0c5a1`'s predecessor, merged into
this branch) put a real, execute-verified `openclaw` into the pixi env
(`pixi run install-openclaw` → `node/node_modules/.bin/openclaw`), and this
issue's own fix depends on shelling out to it. This docstring is a deliverable
of this feature, not incidental — it currently asserts the premise the new
test exists to falsify.

`src/robot_brain/README.md:129-138` (step 6, "Verify the config OpenClaw
actually parsed") has the matching stale prose: it frames `openclaw config
schema` / `openclaw agents list --bindings` as something to run **on the Pi**
because "OpenClaw is not installed on this laptop" (README:77-80, the
"Not run from this worktree" framing of the whole install section). That
framing is still *partially* true — the Pi-specific bits (steps 3/4/5/7/8:
hard-coded SSH paths, the Telegram binding, actually driving the robot) are
genuinely unverifiable from here — but "does this build accept these fields"
(README:132) is now exactly what the new test checks, from the laptop, in CI-
adjacent form (well, laptop-gate form — see below). Whether/how to touch this
paragraph is open question 4 below; at minimum the sentence "Fields most
likely to differ between builds, and worth checking here" (README:134) is now
half-automated rather than fully manual.

## Prior art for "tool may not be installed" in tests

**None in `src/`.** `grep -rn "skipif\|pytest.mark.skip\|importorskip"` across
`src/` and `scripts/` returns nothing — no package in this repo currently
skips a test based on an optional external tool. `openclaw`/`node` would be
the first.

`scripts/check_test_integrity.py` (not test code, but the guard those tests
run under) explicitly models and names this case: its docstring
(`check_test_integrity.py:412-416`) calls out "`pytest.importorskip` on a
missing dependency, a blanket `@pytest.mark.skip`, a hardware-gated suite" as
the *expected shape* of a legitimately-skipped suite, and has a dedicated
failure mode, `_STATUS_ALL_SKIPPED` (`:471-476`), for when **every** collected
test in a package is skipped — "a suite in which no test body executed is the
same hollow green as an empty one, and colcon calls both of them success."

That rule is scoped to the whole **package's** JUnit results, summed
(`audit_package`, `check_test_integrity.py:407-489`), not per file or per
test. `robot_brain` currently collects ~38 non-linter tests; one more test
that's occasionally skipped would not come close to tripping
`_STATUS_ALL_SKIPPED`. Nor would it trip the baseline ratchet: `non_linter`
counts tests *collected*, not *executed* (`parse_xunit`,
`check_test_integrity.py:316-320`, and the docstring at `:426-428`
says so explicitly: "The count is of tests collected, not executed, so a
legitimately skipped test does not trip the ratchet"). So **mechanically**,
`pixi run test` stays green either way — skip or hard-fail — as long as the
package as a whole keeps producing real results.

The actual tradeoff (open question 1, below) is not mechanical, it's about
what the test is *for*: the issue asks for a guard against silent schema
drift specifically. A skip means that guard evaporates in exactly the
environment least likely to have caught it manually (a fresh worktree/CI-like
box without `node/`), which is the same failure mode this issue exists to
fix. A hard failure means `pixi run test` for this whole package now depends
on a gitignored, npm-installed artifact (`node/node_modules/.bin/openclaw`)
being present — which, per `status.md`'s step-2 log and `DEVELOPMENT.md`,
*is* expected to be true on the laptop test-runner (the authoritative gate,
per `CLAUDE.md`'s "the laptop is the test gate" section) since `pixi run
install-openclaw` is documented setup, but is not automatically true, e.g.,
right after a fresh `pixi install` with no explicit `install-openclaw` step.

## Other gotchas

- **Do not invoke the `openclaw` pixi task from a test.** `pixi run openclaw`
  has `depends-on = ["install-openclaw"]` (`status.md:64-67`) — an npm/network
  step. A test must invoke `node/node_modules/.bin/openclaw` directly; it
  works because the test process already runs inside the activated pixi env
  (where `node` is on `PATH` — `status.md`'s opening paragraph: `node` is
  *only* on `PATH` inside the pixi env).
- **Hermetic state dir.** `openclaw config validate` doesn't rewrite the
  config, but does write a state sqlite DB unless `OPENCLAW_STATE_DIR` is set;
  `HOME` should also be redirected to a scratch dir to catch npm droppings
  (`status.md:58-63`, both empirically verified by the manager). I
  independently re-ran `config schema` (not `validate`) the same way —
  `pixi run bash -c "export HOME=$(mktemp -d); export
  OPENCLAW_STATE_DIR=\$HOME/state; node/node_modules/.bin/openclaw config
  schema"` — exit 0, confirms the pattern also works for `schema`, not just
  `validate`.
- **`sandbox.mode`'s enum values carry no description in the schema.** I
  pulled `properties.agents.properties.list.items.properties.sandbox` out of
  the live `config schema` JSON myself (same hermetic-HOME invocation as
  above) — the `mode` and `workspaceAccess` enums are plain `anyOf` /
  `const` lists with **no `title`/`description`** (unlike some other fields in
  the same object, e.g. `docker.gpus`, which does carry a description — so
  the schema *can* carry these, it just doesn't for `sandbox.mode`). This
  means "what does `non-main` vs `all` actually mean" is not answerable from
  the schema alone; it's an OpenClaw-docs/behavior question, out of reach of
  this repo's tooling. Relevant to open question 3.
- **The fragment is a merge fragment, not a whole config**
  (`test_openclaw_config.py:67-73`, `README.md:18-20`): only 3 top-level keys
  (`mcp`, `agents`, `bindings`), and `status.md:55-57` confirms validating the
  bare fragment directly (pointing `OPENCLAW_CONFIG_PATH` at it) is legitimate
  because every other top-level key is schema-optional. The new test can
  validate the shipped file as-is; it does not need to synthesize a full
  config around it.
- **`robot_brain` has no ROS/rclpy dependency and must stay that way**
  (`test_no_ros_runtime.py`, `robot_brain/__init__.py:21` "Pure Python:
  importing this package neither needs nor starts a ROS graph"). The new test
  shells out to a Node binary, not to ROS, so this shouldn't be at risk, but
  it's the kind of invariant a red-team pass will check for.

## Open questions

1. **Skip vs. hard-fail when `node/node_modules/.bin/openclaw` is absent.**
   Options: (a) `pytest.mark.skipif` / `importorskip`-style guard — the test
   is a no-op on a box without `install-openclaw` run, matching "no existing
   test in this repo depends on an optional external tool" being the status
   quo; tradeoff: the drift-guard this issue exists to add can silently not
   run on exactly the kind of fresh/CI-like box where a human is least likely
   to notice by hand. (b) Hard failure (no skip) — the guard is always live;
   tradeoff: `pixi run test` for `robot_brain` now hard-depends on a
   gitignored npm artifact, so anyone running the suite without first running
   `pixi run install-openclaw` gets a failure that looks like a code bug.
   Given `CLAUDE.md`'s "laptop is the test gate" model and that
   `install-openclaw` is already documented setup (`DEVELOPMENT.md`,
   `README.md` in the repo root per commit `7d0c5a1`), is the binary's
   presence something the implementer may simply assume on the gating
   machine, or must the test degrade gracefully?

2. **How the test finds the `openclaw` binary and the repo root.** The binary
   lives at `<repo root>/node/node_modules/.bin/openclaw`; the test process's
   CWD under `colcon test` is `src/robot_brain` (empirically observed, see
   above), and `robot_brain.__file__` resolves through a `--symlink-install`
   symlink whose `..`-walking behavior differs between `os.path.normpath`
   (lexical) and `os.path.realpath`/`Path.resolve()` (kernel-resolved,
   correct one for this case). No existing code in the repo walks upward from
   `__file__` to a repo root — nothing to copy. Options: (a) resolve via
   `Path(robot_brain.__file__).resolve()` and walk up a fixed number of
   parents (repo → src → robot_brain → robot_brain, so 3 levels up from the
   *resolved* package dir); (b) walk up from `resolve()`d `__file__` looking
   for a marker file (`pixi.toml`) rather than a fixed depth, more robust to
   the package/test being relocated; (c) read an env var
   (`OPENCLAW_BIN`/similar) with a `__file__`-derived default, letting a
   future non-worktree layout override it. Needs a decision because it's the
   one piece of genuinely new plumbing this feature needs that nothing in the
   repo already does.

3. **`sandbox.mode`: `"all"` (the issue's suggestion) vs `"non-main"`.** The
   schema enum is `off|non-main|all`, undocumented in the schema itself (see
   gotcha above). The robot agent's only entry point is a Telegram binding
   (`openclaw.robot.json:50-58`) — there is no notion of "the main session" vs
   spawned sub-sessions visible anywhere in this repo's config or code, so
   `non-main` (sandbox everything except a/the "main" session) vs `all`
   (sandbox every session) may be operationally identical for this agent, or
   may not — depends on whether OpenClaw ever spawns a sub-session for this
   agent (e.g. per-message, per-tool-call) that "main" would exclude. Neither
   this repo nor the schema says. The issue names `all` as its proposed
   value; nothing here confirms or contradicts it being the *right* choice
   versus merely *a valid* one. Given `sandbox.workspaceAccess: "ro"` covers
   the "read-only" intent the current (broken) config was reaching for, does
   `mode` even need to be maximally restrictive (`all`), or does `non-main`
   suffice and is closer to whatever OpenClaw's own default/recommended
   posture is?

4. **Does the validate-shellout test belong in `test_openclaw_config.py` or
   its own file?** In favor of the same file: it's conceptually "one more
   thing we can now honestly check about the fragment," and the file's
   docstring already needs editing regardless (see above) — keeping the
   correction and the new capability together tells one coherent story. In
   favor of a new file (e.g. `test_openclaw_validates.py`): it's the only test
   in the package that shells out to a non-Python, gitignored, optionally-
   present binary — different failure mode, different skip semantics (open
   question 1), arguably different enough to isolate so a reader doesn't have
   to hold "pure-Python fragment introspection" and "npm-binary subprocess
   probe" in the same mental model while reading one file. `test_no_ros_
   runtime.py` in this same package already sets the precedent of a separate
   file for "the one test that shells out to something else," which leans
   toward a new file, but it's not identical (that file shells out to
   `sys.executable`, always present, no skip logic at all).

5. **Does `README.md`'s step 6 need editing, and if so how much?** The
   "Verify the config OpenClaw actually parsed" paragraph (`README.md:129-
   138`) is written as an entirely-manual, entirely-on-the-Pi verification
   step. After this fix, `openclaw config schema`/`config validate` are
   partially automated from the laptop (the new test). Is step 6 updated to
   say so (e.g. "the shape is checked automatically; what's still unverified
   here is whether *your* Pi's build agrees" ), left as-is (since the Pi-
   specific unknowns — bindings, SSH, the actual agent behavior — are still
   entirely manual and step 6 is mostly about those), or is this out of scope
   for this issue (README prose isn't in the owned paths list, only the two
   files are)?
