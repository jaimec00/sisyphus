# Status — #56 Ops hardening (close the CI-green-but-deploy-broken class)

Branch: `feat/i56-ops-hardening-close-the-ci-green-but-dep`
Worktree: `/home/sisyphus/worktrees/i56-ops-hardening-close-the-ci-green-but-dep`

| Phase | State |
|---|---|
| 0. Sync | done — branched from `origin/main` @ `e13c3e6`, clean |
| 1. Brief | done — issue #56 read (JSON form), body non-empty |
| 2. Provision + probe | done — **no new dependency**; probed installed `mcp` 2.x client API and prototyped the launcher end-to-end (see "Manager probes") |
| 3. context-explorer | done — `context.md` |
| 4. Manager rulings | done — R1–R11 below |
| 5. implementer | done — 6 commits, `implementation.md` |
| 6. red-team round 1 | done — `red_team.md`: 2 BLOCK, 10 NOTE |
| 7. fix round 1 | done — both BLOCKs fixed; **scoped pass on the fix commit: 0 BLOCK, 3 NOTE** (`red_team_fix_round1.md`); 2 NOTEs applied |
| 8. test-runner | done — PASS: 694 tests, 0 errors/failures/skips, AUDIT PASSED, all stages passed; `boot-smoke` and `check-provisioning` also green standalone. Logs: `.dev/runs/i56-ops-hardening/20260812-204246/` |
| 9. D24 re-read + PR | done — **PR #57**, green against current main (`origin/main` @ `e13c3e6`, no rebase needed) |
| 10. Comments | done — follow-ups on issue #56, retro on PR #57 |

**READY.** PR: https://github.com/jaimec00/sisyphus/pull/57

## Step 9 — durable-decision re-read (this feature's own task 4, dogfooded)

D24 re-read against the diff **as it landed**, not as planned. Every claim checks
out: `REQUIRED_PACKAGE = 'robot_world'` at `scripts/tests/test_boot_smoke.py:43`
backs the "and does" in the boot-smoke bullet; the `src/` enumeration in the
rationale matches `git diff origin/main --name-only -- src/` exactly (four files,
no implementation module); no changed line in `src/` mentions a seam symbol.
The accepted-gaps bullet was amended twice during the run — once by the
implementer's own re-read (round 1), once after the laptop login-shell check
became a verified fact rather than an assumption.

Blockers: none.

## Red-team round 1 — manager verification of the BLOCKs

The red-team subagent ran **without a Bash tool** (`.claude/agents/red-team.md`
grants Read/Grep/Glob/Write only), so it correctly labelled both BLOCKs
"UNEXECUTED" and shipped falsification commands instead of results. I ran them.
**Both BLOCKs are real.**

**B1 — the shipped fragment's launch command is a silent no-op. CONFIRMED.**
`man ssh`: *"arguments will be appended to the command, separated by spaces,
before it is sent to the server"* — ssh does not re-quote, so the remote login
shell receives the flattened string and re-parses it. Simulated:

```
$ bash -c "bash -lc exec echo HELLO-UNQUOTED"   # the shape the fragment ships
(no output)  rc=0
$ bash -c "bash -lc 'exec echo HELLO-QUOTED'"   # quotes embedded in the arg
HELLO-QUOTED rc=0
```

`bash -c` takes the first word after `-lc` as the whole command string and makes
the rest `$0`, `$1`, …; the remote runs a bare `exec`, a no-op, and **exits 0
having started nothing**. Worst-case failure for this PR specifically: a launch
path that reports success and serves no tools — the very class #56 exists to
kill.

Two qualifications the red-team could not establish: (a) the shape is
**pre-existing**, not introduced here — the old `PYTHONPATH=… exec pixi …`
string has the identical disease (`bash -c "bash -lc FOO=bar exec echo HELLO"`
→ no output, rc=0). It is still a BLOCK for this PR: we own that line now and
Sisyphus copies from this template post-merge. (b) `src/robot_mcp/README.md`
claims the shipped command "was checked"; `implementation.md:167-171` actually
checked the *locally quoted* variant, a different string. The false verification
claim gets fixed alongside the command.

**B2 — the new gate tests are not ratchet-protected. CONFIRMED.**
`scripts/test_baseline.json` reads `_workspace_tooling: 111`; the two new files
add exactly 14 tests (`grep -c '^def test_'` → 7 + 7); the ratchet fails only on
`non_linter < baseline` (`check_test_integrity.py:487`). So deleting both new
test files leaves 111 == baseline and `pixi run test` stays green — the entire
gate this feature adds can be removed without the guard noticing.
`context.md` §2 told the implementer additions never need `--update-baseline`;
that is wrong, and `check_test_integrity.py:26-27` says the opposite
("whenever tests are legitimately **added** or removed").

## Fix-round-1 direction given to the implementer

Both BLOCKs, plus NOTEs **N1** (the negative assertion does not inspect
`server()['env']` — a hole in the one assertion whose job is to be un-foolable),
**N4** (a dangling symlink is reported as "is missing"), **N8** (step 7 must say
whether the scoped fix-pass counts against the 2-round budget — it does not),
and **N9** (D24 prose drifts from the diff; task 4 dogfooding itself and
scoring a real hit).

**N3 is declined** — the guard firing on `--packages-select` runs is R7 working
as ruled (narrow, hard-fail, no skip). N2/N5/N6/N7 left to the implementer's
judgment, explicitly "only if cheap, do not churn".

B1 was handed over with the step-7 heuristic attached: *a fix that corrects a
claim in N places is a strong prior it is wrong in an N+1th* — the launch-command
shape is spelled out in the JSON fragment, both READMEs (three times between
them), the launcher's header comment, D24, and the config test. Fix as a set;
enumerate the set in `implementation.md`.

---

## Manager probes (execute-verified, 2026-08-12)

No new third-party dependency, so step 2's provisioning does not apply. Two
mechanics the rulings below depend on were nevertheless verified by *running*
them, not recalled:

**P1 — pixi `depends-on` aborts the parent task and propagates the exit code.**
Probe (`/tmp/pixidep`): `main = { cmd = "echo MAIN-RAN", depends-on = ["guard"] }`
with `guard` exiting 3. Result: `GUARD-RAN` printed, `MAIN-RAN` **not** printed,
`pixi run main` exited **3**. So a `pretest` guard wired as a `depends-on` of the
`test` task genuinely front-runs and short-circuits the suite (R6).

**P2 — a self-discovering shell launcher boots the real server, and bites when a
package is dropped.** Prototype launcher (repo root = `$(cd "$(dirname
"${BASH_SOURCE[0]}")/.." && pwd)`, glob `"$repo"/src/*/package.xml`, join the
dirnames onto `PYTHONPATH`, `exec python -m robot_mcp "$@"`) placed at
`/tmp/lp/scripts/launch.sh` over a `/tmp/lp/src/` tree of **symlinks** to the
real `src/<pkg>` dirs. Driven by a real MCP client with `PYTHONPATH` *stripped*
from the child env, so only the launcher's own discovery could work:

- all 9 packages linked → `initialize()` returned `server_info.name == 'robot_mcp'`;
- `robot_world` symlink removed → child died with
  `ModuleNotFoundError: No module named 'robot_world'` raised from
  `robot_backends/mock_world.py:35`, and the handshake failed.

Two consequences the implementer may rely on: (a) resolving the repo root from
`${BASH_SOURCE[0]}` **without** `readlink -f` is what lets a test stand up a fake
repo root, and is therefore load-bearing, not incidental — document it as such;
(b) the "prove it bites" criterion is reachable as a hermetic test that never
mutates the real `src/` tree.

**P3 — client API, confirmed against the installed `mcp` (2.x) in this worktree.**
`mcp.StdioServerParameters(*, command, args=[], env=None, cwd=None,
encoding='utf-8', encoding_error_handler='strict')`;
`mcp.stdio_client(server, errlog=sys.stderr)` → async CM yielding
`(read_stream, write_stream)`; `mcp.ClientSession(read, write)` →
`await session.initialize() -> types.InitializeResult`. All three are top-level
`mcp` exports (as `src/robot_mcp/test/test_stdio_transport.py:20` already
imports them).

---

## Manager rulings

Binding, but **not assumed correct**. If you believe a ruling is wrong, escalate
to me in-process — do not silently deviate, and do not comply into a bug.

### R1 — The launcher is `scripts/robot-mcp-launch.sh` (bash), and it does discovery *only*.
Resolves OQ1. It computes `PYTHONPATH` from `src/*/package.xml` and `exec`s
`python -m robot_mcp "$@"`. It does **not** invoke `pixi run` itself.

Rationale: a Python entry point cannot fix its own interpreter's import path
before `import robot_mcp` without re-exec'ing, and the brief names the shell form
first. Keeping pixi *out* of the launcher keeps two separable jobs separate —
**pixi supplies the environment, the launcher supplies discovery** — so the
deployed command becomes

```
ssh -T laptop "bash -lc 'exec pixi run --frozen --manifest-path <repo>/pixi.toml <repo>/scripts/robot-mcp-launch.sh'"
```

> **Corrected after red-team round 1 (B1).** As first written, this ruling
> spelled the command `ssh -T laptop bash -lc 'exec pixi …'` — without the outer
> quotes. That form is broken: `ssh` appends its arguments "separated by spaces,
> before it is sent to the server" (`ssh(1)`) and never re-quotes, so the remote
> `bash -c` takes only `exec` as its command string and runs a no-op that
> **exits 0 having started nothing**. The whole remote command must be **one**
> argument carrying its own quoting. The implementer flagged that this file was
> the 11th place the bad shape appeared and that anyone copying from it would
> reintroduce B1 — correct, and fixed here.

and the boot-smoke (already inside the pixi env under `pixi run test`) exercises
*the identical launcher code path*. Baking `pixi run` into the launcher would
instead force the smoke test to nest `pixi run` inside `pixi run` and would make
a fake-repo-root test need a fake pixi manifest. Do **not** add env-sniffing
("am I inside pixi?") — that is the kind of magic that hides a broken deploy.

Requirements: `set -euo pipefail`; `#!/usr/bin/env bash`; the `# name.sh —
purpose` header block per repo convention (§10 of `context.md`); repo root from
`${BASH_SOURCE[0]}` **without** symlink resolution (P2a — comment *why*);
existing `PYTHONPATH` is appended, not clobbered; **fail loudly** (non-zero, on
stderr, `die`-style with the script name as prefix) if discovery finds zero
packages — a launcher that silently execs with an empty path is the same class
of bug this issue exists to kill. Executable bit set.

### R2 — Discovery keys off `src/*/package.xml`, and every discovered package goes on the path.
No allowlist, no filter, no "only the ones robot_mcp needs". The presence of a
manifest *is* the criterion, matching the colcon graph that already keeps
`pixi run test` green. Skeleton packages (`robot_bringup`, `robot_description`,
`robot_perception`) going on the path is harmless and is the point: a package
added tomorrow needs no edit anywhere.

### R3 — The in-repo openclaw fragment **is** repointed at the launcher. (Overrides context.md's lean toward "leave it".)
Resolves OQ2. Edit `src/robot_brain/robot_brain/openclaw/openclaw.robot.json` so
the launch string drops the hand-written `PYTHONPATH=…` entirely and calls
`<repo>/scripts/robot-mcp-launch.sh` through pixi (shape in R1).

Reasoning: the acceptance criterion defines its own terms — *"No `src/` behavior
change; **the seam** (RobotBackend/Observation/SkillResult/SCHEMA_VERSION) is
untouched."* A launch-command string inside a config fragment is not the seam.
The out-of-scope item is the **Pi's** `~/.openclaw/openclaw.json`, which the
issue says "lives outside the repo"; this fragment is the in-repo *template*
Sisyphus hand-merges from (`src/robot_brain/README.md:100-117`, and
`test_openclaw_config.py:89-95` asserts it is a merge fragment). Leaving the
template hand-listing packages would ship a launcher whose whole purpose is to
kill hand lists while the repo's own copy of that hand list stays **live-broken**
— it is missing `robot_world` *today* (`context.md` §0), and Sisyphus's post-merge
repoint would have no correct in-repo source to copy from.

This is the one ruling most likely to be contested. I have flagged it explicitly
for the PR body and the issue comment so Sisyphus can veto it at merge.

### R4 — `test_openclaw_config.py`'s hand list dies and is replaced by a *negative* assertion.
Delete `REQUIRED_PACKAGES` (`:42`) and rewrite
`test_the_launch_command_carries_every_package_the_server_needs` into a test that
the launch command (a) invokes `scripts/robot-mcp-launch.sh`, and (b) contains
**no** literal `PYTHONPATH=` and no `/src/<pkg>` package list. The negative form
is the drift-proof one: the old test could only ever assert the list it was
told about, which is exactly why it agreed with the broken config. Keep the
docstring honest about *why* the assertion inverted.

Also fix the neighbouring `assert 'python -m robot_mcp' in command` (~`:300`) —
the launcher owns that now; assert on the launcher instead. Adjust
`src/robot_brain/README.md` step 3 accordingly (it currently tells a human to
hand-edit **three** things; with the launcher it is two — the `--manifest-path`
and the ssh alias). Update `src/robot_mcp/README.md:104-141` the same way:
the documented one-liner and the MCP client JSON must both go through the
launcher, and the stale "Add `robot_world` to the PYTHONPATH list above" note at
`:143-144` should go, since there is no longer a list to add it to.

If removing a test drops `robot_brain` below its baseline (48), re-cut the
baseline with `python scripts/check_test_integrity.py --update-baseline` and
commit `scripts/test_baseline.json` (R11).

### R5 — Boot-smoke lives in `scripts/tests/test_boot_smoke.py`, plus a `boot-smoke` pixi task.
Resolves OQ3. It belongs beside the launcher it tests, and `run_tooling_tests`
(`check_test_integrity.py:694-715`) already collects everything in
`scripts/tests/` into the gate under `_workspace_tooling` — so "in the gate"
comes free, with no new stage in `main()`'s `stages` dict and no changes to
`FakeWorkspace`. Add a convenience `boot-smoke` pixi task pointing at that file
so it is runnable standalone (the brief's "pixi task **and/or** test").

Do **not** add a stage to `check_test_integrity.py`: that file's job is test
*integrity accounting*, and a boot check there would need its own driver-test
surgery for no gain.

Two implementation notes from P2/P3:
- Write the handshake as a **plain sync test calling `anyio.run(...)`**. Do not
  rely on `pytest.mark.anyio` / an `anyio_backend` fixture — `scripts/tests/`
  has no such conftest and this needs no plugin config. Verified working in P2.
- Strip `PYTHONPATH` (and `ROS_DOMAIN_ID`, `ROBOT_WORLD_STATE`,
  `ROBOT_WORLD_SEED` — the `INHERITED_ENV_TO_DROP` set) from the child env.
  **Do not** reuse `mcp_fixtures.clean_environment()`: it sets `PYTHONPATH` from
  the *test runner's* `sys.path`, which would let the child import everything
  regardless of what the launcher discovered — the test would pass with the
  launcher's discovery entirely broken. This is the single most important
  correctness point in the whole feature; state it in a comment.
- Bound it with a timeout (`anyio.fail_after`), same reasoning as
  `TRANSPORT_TIMEOUT_SECONDS = 30.0` in `test_stdio_transport.py:28-31`.

### R6 — Provisioning guard: a `scripts/` Python check wired as `depends-on` of the `test` task.
Resolves the mechanism half of OQ8/OQ9. Add `scripts/check_provisioning.py`
(Python, so `test_lint.py` lints it and `scripts/tests/` can test it) and convert
the `test` task to the table form:

```toml
test = { cmd = "python scripts/check_test_integrity.py", depends-on = ["check-provisioning"] }
check-provisioning = "python scripts/check_provisioning.py"
```

P1 confirms this short-circuits `test` and propagates the exit code. Leave
`test-audit` **without** the dependency — re-reading existing XML needs no
provisioning. Leave `build` alone.

### R7 — The guard is narrow, hard-fails, and prints the exact command.
Resolves OQ8/OQ9. Check exactly one condition for now: the OpenClaw CLI at
`node/node_modules/.bin/openclaw` (resolve it the way
`test_openclaw_validates.py:58,83-87` does — the `pixi.toml`-marker repo root and
`OPENCLAW_RELATIVE_PATH`) is missing or not executable. **Hard fail** — no skip,
no warning-and-continue; `test_openclaw_validates.py:28-33` sets the precedent
("A drift guard that quietly turns itself off … is the same lie"). Message must
name the failing condition, the resolved path, and the literal remediation
command `pixi run install-openclaw` on its own line so it can be copy-pasted.

Structure it as a **list of checks with one entry**, so the next un-provisioned
condition is an append rather than a refactor — but do **not** add `.pixi/` or
`build/` checks now (`.pixi/` is moot: `pixi run` cannot start without it;
missing `build/` is already handled gracefully by `audit_package:430-436`).

### R8 — Prove-it-bites is a real integration test, not only a unit test of discovery.
Resolves OQ5. Acceptance criterion 1 is literal — *"dropping a required `src/`
package from discovery must make this step fail"* — so the load-bearing test must
spawn the real launcher and observe the real failure. Use the P2 recipe: build a
fake repo root under `tmp_path` with `scripts/<the real launcher, copied>` and
`src/<pkg> → symlink to the real package dirs`, then assert (a) the full set
handshakes, and (b) with one required package's symlink removed, the handshake
fails / the child exits non-zero. Never mutate the real `src/` tree.

A cheap unit test of the discovery logic alone is welcome **in addition**, not
instead. Note in the test docstring which package is dropped and why it is
"required" (`robot_world` is the #55 package and is imported unconditionally at
`server.py:73` — a good choice precisely because it is the historical failure).

### R9 — `run-feature.md` edits: sharpen step 6, extend step 7, add the decisions re-read to step 9.
Resolves OQ4. Tasks 4/5a/5b land in `.claude/commands/run-feature.md` only:

- **5a (step 6, `:47-50`)** — *sharpen, do not duplicate.* The existing text
  already names rulings as targets; add the missing imperative: state each ruling
  as a **falsifiable claim** and instruct the red-team to **disprove it
  empirically — run the code, don't reason about it**.
- **5b (step 7, `:51-53`)** — new: fix commits get their **own red-team pass
  scoped to just the fix diff**, because round-1 fixes introduce genuinely new
  logic that the round-1 review never saw. Encode the heuristic verbatim in
  spirit: *a fix that corrects a claim in N places is a strong prior it appears
  in N+1* — so a scoped pass must go looking for the N+1th.
- **4 (step 9, `:56-60`)** — new, pre-"ready": **re-read the
  `docs/design/decisions.md` entry against the final diff**, because the durable
  decision log is the least-reviewed, longest-half-life artifact and in #55 a
  correction landed only in the ephemeral `docs/features/<slug>/` copy.

Do **not** touch `.claude/agents/red-team.md` — 5a/5b are about how the *manager
dispatches*, not the subagent's baked-in prompt. Match the file's existing voice
and formatting; keep the step numbering as-is.

### R10 — One line in `DEVELOPMENT.md`, for the decisions re-read only.
Resolves OQ7. `DEVELOPMENT.md:69-98` enumerates the same 10 steps, so a reader
who skims only it would miss a new *loop step*. Add the decisions-re-read there
at that file's prose altitude. Leave the red-team wording nuances (5a/5b) to
`run-feature.md` — they are dispatch mechanics, not loop shape, and duplicating
them creates the second source of truth this issue is about.

### R11 — Add **D24** to `docs/design/decisions.md`.
Resolves OQ6. #51 (a dependency addition) rightly got no decision, but this is
closer to D20: a durable commitment about *how the system is operated*, namely —
**the launch path discovers packages; hand-maintained package lists on the
deploy path are banned, and the gate boots the server through the same launcher
the deployment uses.** That is precisely the invariant a future contributor
could unknowingly undo, and recording it is the cheapest possible guard.
Follow the file's format exactly: a `## 2026-08-12 — <title>` section with a
`- **D24 — <short title> (closes #56).** <prose>` bullet; append, never edit
history (`decisions.md:3`).

Write D24 **last**, after the diff is final, and then re-read it against that
diff — this feature's own task 4, dogfooded on its first run.

---

## Scope

Owned paths: `scripts/`, `pixi.toml`, `.claude/commands/run-feature.md`,
`DEVELOPMENT.md`, `docs/design/decisions.md`, `docs/features/i56-ops-hardening/`,
plus — per R3/R4 — `src/robot_brain/robot_brain/openclaw/openclaw.robot.json`,
`src/robot_brain/test/test_openclaw_config.py`, `src/robot_brain/README.md`,
`src/robot_mcp/README.md`, `scripts/test_baseline.json`.

Untouched, and must stay untouched: every `src/**/*.py` implementation module.
The seam (`RobotBackend` / `Observation` / `SkillResult` / `SCHEMA_VERSION`) is
not to be modified.

## Escalations

None yet.
