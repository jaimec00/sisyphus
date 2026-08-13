# Red-team — scoped pass on fix round 1 (#56)

Scope: `git diff 30ef31a..HEAD` — commit `868381d`, "fix(round 1): the shipped
launch command actually starts the server; ratchet the new gate". The six
commits before it are round 1's business and were not re-reviewed.

**Every claim below was executed.** This reviewer had a shell and used it; there
are no unexecuted hypotheses in this report. Probe scripts and captured output
live under `/tmp/rt56/` (`f1_probe.sh`, `mutate.py`, `f4b.py`, `f7.py`,
`full_test.log`, `fakebuild/`).

**Verdict: 0 BLOCK, 3 NOTE.** The fix is sound. B1 and B2 are really fixed, the
N+1 enumeration is complete as far as I can drive it, and D24 is accurate line
by line. The three NOTEs are cheap hardening, not defects on the deploy path.

**Worktree untouched.** `git status --porcelain` at the end shows only
`M docs/features/i56-ops-hardening/status.md` — the manager's own concurrent
edit, not mine. This file is my only write.

---

## F1 — the shipped launch string actually starts a server. **CONFIRMED.**

Simulated what `sshd` does: flattened `args` after the destination with spaces,
ran `bash -c "<flattened>"`, piped one `initialize` frame in.

```
NEW (one args element, as shipped, repo path substituted for this worktree)
  rc=0   stdout: 1024 bytes, 1 line — the initialize result, serverInfo.name=robot_mcp
         stderr: pixi's manifest-deprecation warning only

OLD (the three-element ["bash","-lc","exec pixi …"] form)
  rc=0   stdout: 0 bytes
         stderr: (nothing)
```

Exactly one JSON-RPC frame and nothing else, as the README claims. The old shape
reproduces the silent-no-op bug against the same launcher. I did not take
`implementation.md`'s word for any of this.

Two things I checked that nobody had:

- **`ssh -T localhost` is not available here** (`Permission denied (publickey,password)`),
  so a real-`ssh` end-to-end probe — the one round 1 proposed — remains
  impossible from this machine. The `bash -c "<flattened>"` simulation is what
  `sshd` does with `$SHELL -c`, and it is the strongest probe available.
- **The `-lc` claim is true on this laptop, and is now verified rather than
  asserted.** `scripts/robot-mcp-launch.sh:28-29` and
  `test_openclaw_config.py`'s docstring state flatly that `-lc` is what puts
  `pixi` on `PATH` for a non-interactive ssh command. It holds:
  ```
  env -i HOME=$HOME bash  -c 'command -v pixi'  → NOT ON PATH
  env -i HOME=$HOME bash -lc 'command -v pixi'  → /home/sisyphus/.pixi/bin/pixi
  ```
  (`~/.bashrc:1-3` is where it comes from.) The laptop half of the hop is
  therefore no longer an assumption. Only the Pi→laptop leg remains unverified,
  which is exactly what README step 4 and D24 now say.

## F2 — `remote_command()`'s destination heuristic. **Worry disproved.**

The concern was that `-o BatchMode=yes` would make the helper treat
`BatchMode=yes` as the destination and then assert on the wrong slice *and still
pass*. It does take the wrong slice — and every one of those cases goes **red**,
not quietly green. Six mutations, run through the real test functions:

| fragment mutation | flattened-command test | one-checkout test |
|---|---|---|
| `-o BatchMode=yes` prepended | FAIL (AssertionError) | FAIL (ValueError) |
| `-p 22` prepended | FAIL | FAIL |
| `-i ~/.ssh/id_ed25519` prepended | FAIL | FAIL |
| `-J jump` prepended | FAIL | FAIL |
| `-o …` placed *after* the destination | FAIL | FAIL |
| `sisyphus@laptop` destination | PASS | PASS |

The failure direction is safe: a future `-o`/`-p`/`-i` flag breaks the build
loudly rather than silently asserting on nothing. See NOTE 2 for the one cost.

## F3 — does the new test bite, and can it be fooled? **Bites. One hole.**

Restoring the old three-element form:

```
FAIL  test_the_flattened_remote_command_is_one_the_remote_shell_can_run
FAIL  test_the_two_absolute_paths_in_the_command_name_one_checkout
```

Wrong shapes that are correctly caught: `bash -c` without `-l`; a trailing
`>/dev/null` inside the quotes; a second command after `;`; a hand list smuggled
in via `cd … && exec` or `env PYTHONPATH=`.

Wrong shapes that **pass**:

- **Fully relative paths** — `bash -lc 'exec pixi run --frozen --manifest-path
  pixi.toml scripts/robot-mcp-launch.sh'` passes all five launch-command tests.
  See NOTE 1.
- A launcher path that exists on no machine (`/nope/scripts/robot-mcp-launch.sh`)
  passes. This is the accepted gap D24 already records — the two absolute paths
  are hand-typed per deployment and nothing in this repo can resolve them. Not a
  finding.

On `shlex.split` vs bash: I looked for a string the two parse differently in a
way that matters. The realistic disagreements (`;`, `|`, `>` as operators;
unexpanded `$VAR` inside double quotes) all either fail the length-3 assertion or
fail the `inner[-1].endswith(LAUNCHER)` assertion. The one that slips through is
the relative-path case above, and that is an absoluteness gap rather than a
parser disagreement.

## F4 — is the negative assertion harder to fool than the list it replaced? **Yes.**

Round 1's N1 widening to `json.dumps(server())` works: a `PYTHONPATH` planted in
the server entry's `env` block is caught, where the old command-string form
would have missed it (verified — that mutation fails the test).

Every realistic reintroduction of a hand list is caught, because the only
variable Python reads is spelled `PYTHONPATH` and the substring check sees it
wherever in the entry it lands:

| reintroduction | caught |
|---|---|
| `"env": {"PYTHONPATH": "<repo>/src/a:<repo>/src/b"}` | yes (N1's fix) |
| `env PYTHONPATH=src/a:src/b` with **relative** paths (no `/src/`) | yes |
| `cd <repo> && exec … env PYTHONPATH=…` | yes (twice) |
| `exec env PYTHONPATH=… pixi run …` | yes |
| a list appended as launcher arguments (`--extra-path <repo>/pkgs/a:…`) | yes, by the length-3 / `endswith` assertions |

A list parked under `agents` rather than `mcp.servers` is not seen — but OpenClaw
does not read the server's environment from there, so it is not a launch path.
Not a finding.

## F5 — the ratchet re-cut. **Correct, and it bites.**

The numbers match a real green run exactly, not `implementation.md`'s account:

```
_workspace_tooling  129 collected  126 non-linter  +0  ok
robot_brain          53 collected   50 non-linter  +0  ok
```

`robot_brain: 50` is right: round 1 removed no tests (the hand-list test was
inverted, not deleted) and the fix added two. No package's baseline went down —
the diff moves exactly two numbers and both go up (111→126, 48→50).

Bite, proved without touching the worktree: I copied `build/` to
`/tmp/rt56/fakebuild`, stripped the 15 `test_boot_smoke` + `test_provisioning`
testcases from `_workspace_tooling/pytest.xml`, and ran the audit against it via
`--build-base`:

```
_workspace_tooling  114  …  111  -15  below-baseline
FAIL _workspace_tooling: 111 non-linter tests, 15 below the baseline of 126 …
AUDIT FAILED   EXIT=1
```

At the old floor of 111 that deletion was green. B2 is genuinely closed.

## F6 — did the `test_boot_smoke.py` changes strengthen or weaken it? **Strengthen.**

**(a) The control test.** Dropping `cwd=str(REPO_ROOT)` and switching
`sys.executable` → `'python'` was the right call and it still fails for the right
reason. `python -c 'import robot_mcp'` under `undiscovered_environment()`:

```
cwd=<repo>                 ModuleNotFoundError: No module named 'robot_mcp'   ← the gate's cwd
cwd=<repo>/scripts         ModuleNotFoundError: No module named 'robot_mcp'
cwd=/tmp                   ModuleNotFoundError: No module named 'robot_mcp'
cwd=<repo>/src             IMPORTED (namespace package, __file__ = None)
cwd=<repo>/src/robot_mcp   ModuleNotFoundError: No module named 'robot_skills'
```

So the verdict *is* cwd-dependent — from `<repo>/src` the import succeeds and the
control goes **red**. That is precisely what the new docstring promises ("if
`cwd` … ever *does* start mattering, this must feel it first"), and red is the
safe direction. `pixi run test` pins the tooling suite to `cwd=repo_root`
(`check_test_integrity.py:715`), so the gate never sees it.

**Does the boot-smoke's own child inherit a cwd that could make `robot_mcp`
importable without discovery?** No. `handshake()` and `run_launcher()` pass no
`cwd=`, so the child inherits the repo root, and `<repo>` holds no top-level
`robot_mcp`. The only cwd that would leak an import is `<repo>/src`, which yields
an empty namespace package with no submodules — the handshake would still fail,
and the control would have gone red first. I could not construct a cwd under
which the boot-smoke passes while discovery is broken.

**(b) `written_repository_root` now `mkdir()`s `src/` separately.** The concern —
that the old "no `src/` at all" case is now untested — is real as coverage but
immaterial as behaviour. All three states hit the same unmatched-glob path and
produce a byte-identical message:

```
absent src/          robot-mcp-launch.sh: no package.xml found under …/src — refusing…   rc=1
empty  src/          robot-mcp-launch.sh: no package.xml found under …/src — refusing…   rc=1
src/ is a plain file robot-mcp-launch.sh: no package.xml found under …/src — refusing…   rc=1
```

Not worth a test. The rename to `test_a_root_with_an_empty_source_tree_refuses_to_launch`
is honest about what it now exercises, which is the whole point of N5.

**(c) The stub interpreter change** (`printf '%s\n' "$@"`) plus the
space-containing `--world-state '/tmp/a world.json'` genuinely closes the
lost-quote hole — the assertion is now on the argv list, not a joined string.

## F7 — the N4 fix in `check_provisioning.py:78-89`. **Correct for every state that matters.**

Nine filesystem states built under `/tmp` and checked against what the guard says:

| state | `exists` / `is_symlink` / `is_file` / `X_OK` | message | true? |
|---|---|---|---|
| missing | F F F F | is missing | yes |
| present + executable | T F T T | *(passes)* | yes |
| present + non-executable | T F T F | is not executable | yes |
| dangling symlink | F T F F | is a broken symlink | yes |
| symlink **chain** to an executable | T T T T | *(passes)* | yes |
| symlink loop | F T F F | is a broken symlink | yes |
| symlink to a non-executable file | T T T F | is not executable | yes |
| symlink to a **directory** | T T F T | is not executable | **no** |
| a real **directory** | T F F T | is not executable | **no** |

Branch order (`exists()` → `is_symlink()` → else) is right, and the symlink chain
case — which neither the report nor the tests mention — behaves correctly. See
NOTE 3 for the two directory rows.

## F8 — D24's "accepted gaps" bullet. **Every claim verified.**

Checked clause by clause against code, not prose:

- "the gate boots the launcher *directly, already inside pixi*" — `handshake()`
  spawns `str(root / LAUNCHER)` with no `pixi` in sight; the tooling suite runs
  under `pixi run test`. True.
- "the `ssh -T <alias>` hop, the two absolute paths and the `pixi run --frozen
  --manifest-path` prefix are **not** executed by any test" — true; nothing in
  the suite invokes `ssh` or the fragment's `pixi run` prefix.
- "the flattened remote command re-parses into an argv that reaches the launcher"
  — that is what `test_the_flattened_remote_command_is_one_the_remote_shell_can_run`
  asserts. True.
- "`ssh` … never re-quotes, which is how the first version … ran a bare `exec`,
  started nothing and exited 0" — reproduced in F1 (0 bytes, rc=0). True.
- "the manifest path and the launcher path name one checkout" — true, and it is
  the only claim with a gap (NOTE 1: absoluteness is not asserted). The prose
  itself is accurate.

The rest of D24, re-read against the diff since it is the longest-half-life
artifact here:

- "`mcp_fixtures.clean_environment()` … hand the child the test runner's own
  `sys.path`" — `mcp_fixtures.py:43` does exactly that. True.
- "six `robot_brain` failures" (`check_provisioning.py` message) — the file
  collects **7** tests, one of which (`test_the_cli_is_installed_where_the_suite_expects_it`)
  only checks the bit rather than running the CLI. Six run it. Exactly right.
- The amended rationale: "the edits inside `src/` are the OpenClaw fragment's
  launch string, `test_openclaw_config.py` …, and the two READMEs" —
  `git diff origin/main --name-only -- src/` returns those four files and no
  others. True.

## F9 — free hunting, the real test number, and the acceptance criterion

**The honest number, run by me:**

```
Summary: 694 tests, 0 errors, 0 failures, 0 skipped
AUDIT PASSED: every expected package collected tests
All stages passed.          EXIT=0
```

Matches `implementation.md`. Both standalone tasks the feature ships also
actually run — checked, because "a command nobody executes" is this issue's whole
bug class: `pixi run boot-smoke` → 7 passed, EXIT=0; `pixi run check-provisioning`
→ EXIT=0.

**Acceptance criterion, independently checked.** `git diff origin/main
--name-only -- src/`:

```
src/robot_brain/README.md
src/robot_brain/robot_brain/openclaw/openclaw.robot.json
src/robot_brain/test/test_openclaw_config.py
src/robot_mcp/README.md
```

The only `.py` is a **test** file, explicitly in scope per R4 — no implementation
module changed. And no changed line in `src/` mentions `RobotBackend`,
`Observation`, `SkillResult` or `SCHEMA_VERSION`:
`git diff origin/main -- src/ | grep -E "^[+-].*(RobotBackend|Observation|SkillResult|SCHEMA_VERSION)"`
returns nothing. **Criterion holds.**

### The N+1 hunt — I could not find a 12th

Grepped the whole repo (`docs/`, root `*.md`, `.claude/`, `scripts/pi/`,
`scripts/*.sh`, every docstring, and the six earlier commits' messages) for
`ssh `, `bash -lc`, `bash -c`, `PYTHONPATH=`, `--manifest-path` and
`robot-mcp-launch`. Eight places spell out a launch or remote command; all eight
are correct:

1. `openclaw.robot.json:11` — one `args` element, quoting embedded. Verified by
   execution (F1).
2. `scripts/robot-mcp-launch.sh:20-21` — the header comment, outer quotes present.
3. `src/robot_brain/README.md:124` — step 4's probe, same shape, here-string on stdin.
4. `src/robot_mcp/README.md:109` / `:115` — local, no `ssh`, no shell in between.
5. `src/robot_mcp/README.md:128-141` — client JSON, direct argv, no shell, no `env`.
6. `src/robot_mcp/README.md:164` — the `--world-state` example (see the aside below).
7. `scripts/pi/dispatch.sh:41,52,58-59` and `scripts/pi/watch-run.sh:41,47,57` —
   every one passes the remote command as a **single** shell-quoted argument.
   Not affected by the B1 disease. Out of this feature's scope and correct anyway.
8. `scripts/start-feature.sh:108` / `start-op.sh:106` — `ssh laptop -t "tmux
   attach -t …"`, one argument. Correct.

`.claude/`, `README.md` and `DEVELOPMENT.md` contain no launch command at all.

The implementer's own enumeration was accurate, including its item 11 (`status.md`
R1) — which the manager has since corrected in place.

---

## NOTE 1 — `test_the_two_absolute_paths_…` never asserts absoluteness

`src/robot_brain/test/test_openclaw_config.py:403-416`. The name promises "the
two **absolute** paths"; the body asserts only `manifest.name == 'pixi.toml'` and
`launcher.parent.parent == manifest.parent`. A fully relative pair satisfies both,
because `PurePosixPath('pixi.toml').parent` and
`PurePosixPath('scripts/robot-mcp-launch.sh').parent.parent` are both
`PurePosixPath('.')`.

Reproduced — this fragment passes **all five** launch-command tests:

```json
"args": ["-T", "laptop",
         "bash -lc 'exec pixi run --frozen --manifest-path pixi.toml scripts/robot-mcp-launch.sh'"]
```

and is broken on the wire: the remote login shell's cwd is `$HOME`, not the
checkout, so `pixi` gets no manifest and the launcher is not found. Failure
surface is the exact one this issue exists to kill — "the agent has no tools",
no error anywhere.

Why NOTE and not BLOCK: the plausible edit is a human retyping *absolute* paths
per README step 3, and that is the drift the test does catch. The fix is one line
in each of the two assertions:

```python
assert manifest.is_absolute() and launcher.is_absolute(), (manifest, launcher)
```

Worth taking — it costs nothing and makes the test's name true.

## NOTE 2 — an `ssh` option in the fragment breaks the tests with a confusing message

`remote_command()` (`test_openclaw_config.py:83-99`) takes the first argument not
starting with `-` as the destination. F2 shows the consequence is loud, not
silent — every `-o`/`-p`/`-i`/`-J` form fails — but the message a future
maintainer sees is an assertion on `['-T', 'laptop', 'bash', '-lc', …]` with no
hint that the *helper* mis-parsed, plus a bare `ValueError` from
`inner.index('--manifest-path')` in the neighbouring test.

`-o BatchMode=yes` and `-o ConnectTimeout=…` are plausible additions here —
`scripts/pi/watch-run.sh:41` already uses both on its own hops. Cheapest honest
fix is not a real option parser but a guard that names the problem:

```python
assert set(args[:destination]) <= {'-T'}, (
    f'{args[:destination]} — this helper only understands value-less flags; '
    f'teach it the new one before adding it to the fragment')
```

NOTE, not BLOCK: nothing ships wrong today and the failure is red.

## NOTE 3 — "is not executable" is false for a directory

`scripts/check_provisioning.py:81-82`. A directory (or a symlink to one) at
`node/node_modules/.bin/openclaw` reports "the OpenClaw CLI is not executable"
while `os.access(path, os.X_OK)` is `True` — directories carry `+x`. That is the
same species of falsehood N4 was raised to fix ("saying *is missing* would be a
falsehood about a path `ls` shows").

Plausibility is much lower than N4's, though: npm writes a symlink to a `.mjs`
there, and nothing in this repo would ever create a directory at that name. If
it is taken, one branch covers it:

```python
elif binary.is_dir():
    state = 'is a directory, not the CLI'
```

I would not block on this, and I would not be upset if it were declined.

---

## Two observations, below NOTE, recorded and not raised

- `src/robot_mcp/README.md:164` — `scripts/robot-mcp-launch.sh --world-state …`
  is the one launcher example without the `pixi run --frozen --manifest-path`
  prefix the section establishes 55 lines above. Run as written outside pixi it
  is `rc=127, exec: python: not found`. It is strictly better than the
  `python -m robot_mcp …` it replaced, `implementation.md` already records the
  behaviour, and it reads as a flag illustration rather than a copy-paste line.
  Mentioning it only so the next reviewer does not re-derive it.
- Commit `bf8f11c`'s message still carries the pre-fix claim that the command was
  verified "by running the new command through `bash -lc`" — the locally-quoted
  variant, which is the false verification the fix corrected in the README. It is
  immutable history and the squash-merge subject will come from the PR, so this
  matters only if the squash body concatenates every message. Not worth a rewrite.
