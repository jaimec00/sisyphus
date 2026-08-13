# Implementation — #56 Ops hardening (close the CI-green-but-deploy-broken class)

Branch: `feat/i56-ops-hardening-close-the-ci-green-but-dep`
Commits (small, each green):

| commit | what |
|---|---|
| `0b9ad33` | `scripts/robot-mcp-launch.sh` + `scripts/tests/test_boot_smoke.py` + `boot-smoke` pixi task |
| `2821d8a` | `scripts/check_provisioning.py` + `scripts/tests/test_provisioning.py` + `test` → `depends-on` wiring |
| `bf8f11c` | the OpenClaw fragment, its test and the two READMEs repointed at the launcher (R3/R4) |
| `cf94057` | `run-feature.md` steps 6/7/9 + one line in `DEVELOPMENT.md` (R9/R10) |
| `73e16db` | D24, written last and re-read against the final diff (R11 / task 4 dogfooded) |
| *(fix round 1)* | B1, B2, N1/N2/N4/N5/N6/N7/N8/N9 — see "Fix round 1" at the bottom |

## Final design

### 1. The launcher — `scripts/robot-mcp-launch.sh`

Bash, per **R1**. It does exactly three things: resolve the repo root from
`${BASH_SOURCE[0]}` **lexically** (no `readlink -f`), glob `src/*/package.xml`
and put each manifest's directory on `PYTHONPATH`, then
`exec python -m robot_mcp "$@"`.

- **No `pixi run` inside it, no env sniffing** (R1). The deployed command is
  `pixi run --frozen --manifest-path <repo>/pixi.toml <repo>/scripts/robot-mcp-launch.sh`;
  pixi supplies the environment, the launcher supplies the path. That is what
  lets the gate run *this exact file* rather than a re-implementation.
- **The no-`readlink` choice is load-bearing and now test-covered.** The
  boot-smoke's fake repo root holds a **symlink** to the real launcher — so
  adding `readlink -f` makes the fake root resolve back to the real one, and
  four tests go red (verified, §"Sabotage B" below). R8 said "copied"; a
  symlink is strictly stronger for that reason and is the one deviation-in-form
  from a ruling — its substance (never mutate the real `src/`) is preserved.
- **Discovery is the manifest and nothing else** (R2): no allowlist, no filter.
  Skeleton packages go on the path too; that is the point.
- **Zero packages is a loud failure**: `die` with the script name on stderr and
  a non-zero exit, naming the tree it searched. It never reaches the
  interpreter (asserted).
- **An inherited `PYTHONPATH` is appended, never clobbered**, and comes *after*
  the discovered entries so a stale copy of a workspace package elsewhere on
  the path cannot shadow the real one (asserted).

### 2. Boot-smoke — `scripts/tests/test_boot_smoke.py` (+ `pixi run boot-smoke`)

Per **R5**: it lives beside the launcher, so `run_tooling_tests` collects it
into the gate as `_workspace_tooling` with no new stage in
`check_test_integrity.py`'s `stages` dict. The convenience task
`pixi run boot-smoke` runs the same file standalone (it needs
`-p no:launch_testing -p no:launch_ros`, the same RoboStack plugin workaround
the per-package `pytest.ini` files use — confirmed by running it without them
and getting `PluginValidationError`).

Seven tests, three of them integration:

- `test_every_package_with_a_manifest_lands_on_the_path`,
  `test_an_inherited_pythonpath_is_appended_not_clobbered`,
  `test_the_servers_own_arguments_are_forwarded`,
  `test_a_root_with_an_empty_source_tree_refuses_to_launch` — cheap unit tests
  of discovery (R8's "welcome in addition"). They make discovery observable by
  shadowing `python` on `PATH` with a stub that prints `$PYTHONPATH` and then
  one argument per line, so nothing boots.
- `test_the_stripped_environment_really_hands_the_child_nothing` — the control:
  `python -c 'import robot_mcp'` in the same child environment **must fail**.
  Without it, a leaked `PYTHONPATH` would silently make everything below
  vacuous.
- `test_the_launcher_boots_a_server_that_answers_initialize` — the gate step: a
  real MCP `initialize` over stdio, `anyio.run(...)` in a plain sync test (no
  `pytest.mark.anyio`, no `anyio_backend` fixture — R5), bounded by
  `anyio.fail_after(30.0)` so a hung server fails rather than stalls.
- `test_dropping_a_required_package_breaks_the_boot` — the acceptance
  criterion. `robot_world` is removed from the fake source tree; the handshake
  must fail **and** the child's captured stderr must contain
  `No module named 'robot_world'`. Asserting the stderr, not just "it raised",
  is deliberate: a hung server also raises (on the timeout), and "it timed out"
  is not evidence that the missing package is what broke it.

**The env-stripping point (the manager's non-negotiable).**
`mcp_fixtures.clean_environment()` is deliberately **not** reused;
`undiscovered_environment()` drops `PYTHONPATH` outright (plus `ROS_DOMAIN_ID`,
`ROBOT_WORLD_STATE`, `ROBOT_WORLD_SEED`, mirroring `INHERITED_ENV_TO_DROP`).
The three env-var names are spelled out rather than imported because this suite
runs outside the workspace packages — which is the point of it.

### 3. Provisioning guard — `scripts/check_provisioning.py`

Per **R6/R7**: Python (so `test_lint.py` lints it and `scripts/tests/` can test
it), wired as `depends-on` of the `test` task; `test-audit` deliberately does
not get it. One check today, held in a `CHECKS` tuple so the next condition is
an append; it checks the CLI is a file **and executable** (an interrupted `npm
install` leaves the name without the bit, and `robot_brain` runs the binary
rather than importing it). Hard fail, no skip. The message names the condition,
the resolved path, why it matters, and `pixi run install-openclaw` on its own
copy-pasteable line.

### 4. The repoint (R3/R4)

`openclaw.robot.json`'s launch string is now
`bash -lc 'exec pixi run --frozen --manifest-path <repo>/pixi.toml <repo>/scripts/robot-mcp-launch.sh'`
as **one** `args` element (the quoting matters — see B1 in the fix round below)
— no `PYTHONPATH`, no package names. `REQUIRED_PACKAGES` is deleted;
`test_the_launch_command_carries_every_package_the_server_needs` became
`test_the_launch_command_leaves_the_package_list_to_the_launcher`, asserting the
*negative* over the whole server entry (`'PYTHONPATH' not in
json.dumps(server())`, likewise `'/src/'`), and
`test_the_launch_command_starts_this_repos_server_over_stdio` now asserts the
launcher — **and that the launcher it names exists in this checkout**, so
renaming or moving the script fails here rather than on the Pi. The rewrite
removed no tests and the fix round added two, so `robot_brain` went 48 → 50 and
`scripts/test_baseline.json` was re-cut (see B2).

Both READMEs were updated: the `robot_mcp` "Run it" section (one-liner, client
JSON — now with **no `env` block at all**, since the path is discovered) and the
stale "Add `robot_world` to the `PYTHONPATH` list above" note is gone with the
list it referred to; `robot_brain`'s deployment step 3 now says **two**
hard-coded paths plus the ssh alias, and states explicitly that there is no
package list to edit.

No `src/**/*.py` implementation module was touched. The seam
(`RobotBackend` / `Observation` / `SkillResult` / `SCHEMA_VERSION`) is untouched.

### 5. Process (R9/R10) and D24 (R11)

`run-feature.md` step 6 (falsifiable claims, "disprove this empirically — run
the code"), step 7 (a red-team pass scoped to the fix diff, carrying the
*N places → N+1th* heuristic), step 9 (re-read `decisions.md` against the final
diff before "ready"). Step numbering unchanged; `.claude/agents/red-team.md`
untouched. `DEVELOPMENT.md` gained the decisions re-read only.

## Verification (exact commands, run in this worktree)

**Acceptance criterion — "boot-smoke fails when a package is removed from
discovery; passes on a normal build."** Three sabotages, each applied to the
real launcher and then reverted:

- **A — the #55 bug re-introduced** (`case "$manifest" in *robot_world*) continue ;; esac`
  inserted into the discovery loop):
  `pixi run --frozen python -m pytest scripts/tests/test_boot_smoke.py -q -p no:launch_testing -p no:launch_ros`
  → `1 failed, 6 passed`; the failure is
  `test_the_launcher_boots_a_server_that_answers_initialize`. (The dedicated
  bites test still passes here — it is *asserting* that removal breaks the
  boot, which it does.)
- **B — `readlink -f "${BASH_SOURCE[0]}"` added** to the repo-root resolution:
  → `4 failed, 3 passed`, including
  `test_dropping_a_required_package_breaks_the_boot`. The no-symlink-resolution
  property is therefore covered, not just commented.
- **C — the theatre scenario** (sabotage A **plus** `PYTHONPATH` removed from
  `DROPPED_ENV`, run with a developer-style
  `PYTHONPATH=<repo>/src/robot_skills:…:<repo>/src/robot_world` exported):
  → `test_the_launcher_boots_a_server_that_answers_initialize` **passes while
  discovery is broken**, exactly as R5 predicted, and the run is only saved by
  the control test and the bites test failing. This is the empirical proof that
  reusing `clean_environment()` would have made the feature theatre.

**Acceptance criterion — "the guard prints the exact install command on a bare
worktree" / P1 (`depends-on` really short-circuits `pixi run test`):**

```
mv node/node_modules/.bin/openclaw node/node_modules/.bin/openclaw.hidden
pixi run test          # EXIT=1, colcon never started (grep -c colcon → 0)
mv node/node_modules/.bin/openclaw.hidden node/node_modules/.bin/openclaw
```

Output was the guard's message alone — the condition, the absolute resolved
path in *this* worktree, and `pixi run install-openclaw` on its own line.

**The deployed command still emits exactly one JSON-RPC frame through a login
shell** (the claim `src/robot_brain/README.md` makes, re-checked for the new
command):

```
printf '{"jsonrpc":"2.0","id":1,"method":"initialize",...}\n' \
  | bash -lc "exec pixi run --frozen --manifest-path $PWD/pixi.toml $PWD/scripts/robot-mcp-launch.sh"
```
→ `rc=0`, **1 line** on stdout, pixi's manifest warning on stderr only.

**Full suite** (`pixi run build` then `pixi run test`, final run on `73e16db`'s
tree): **EXIT=0 — 691 tests, 0 errors, 0 failures, 0 skipped; AUDIT PASSED; All
stages passed.** Per-package: `_workspace_tooling` 125 non-linter (**+14**),
`robot_brain` 48 (+0), everything else +0. Nothing is red; nothing was skipped;
`scripts/test_baseline.json` needed no change.

## Rulings

Followed as written: **R1, R2, R3, R5, R6, R7, R9, R10, R11**.

Followed with one deliberate strengthening, flagged rather than done silently:

- **R8** says the fake repo root should hold the *copied* launcher. It holds a
  **symlink** instead. With a copy, `readlink -f` would resolve to the copy's
  own directory and the sabotage-B regression would be invisible; with a
  symlink it resolves back into the real tree and four tests catch it. The
  ruling's stated intent — a hermetic test that never mutates the real `src/` —
  is fully preserved (every `src/<pkg>` is still a symlink and nothing writes
  into the real tree). Flagging it here because it is a deviation in form.

Pushed back on: nothing. **R4** was followed in substance with one addition:
besides the negative assertions it demands, the rewritten test also asserts the
launcher path it names is a real file in this checkout — a hand-written path is
the same class of drift as a hand-written list, and it costs one line.

## Surviving notes for the manager (not blockers, not filed by me)

1. **`scripts/start-op.sh` never runs `install-openclaw`** (context §6), so an
   operational-agent worktree that runs `pixi run test` now stops at the new
   guard with a clear message rather than six failures — an improvement, but the
   underlying provisioning gap is still there. Whether op worktrees should
   provision, or the guard should be relaxed for them, is a decision above my
   scope. `scripts/start-feature.sh` provisions best-effort (`|| echo …`), so a
   network hiccup still yields an unprovisioned worktree — now caught in
   seconds instead of minutes.
2. **The Pi-side repoint is still Sisyphus's post-merge job** (explicitly out of
   scope). Until it happens, the Pi's `~/.openclaw/openclaw.json` keeps the old
   four-package `PYTHONPATH` string and stays broken; the in-repo template it
   is merged from is now correct, which is what R3 was for.
3. **The launcher hard-codes `python`** (resolved from `PATH`, i.e. whatever
   pixi put there). That is correct inside the pixi env and is what the gate
   exercises; a caller who runs it outside any env gets bash's
   `python: command not found`, which is legible but not as loud as `die`.

---

# Fix round 1 (red-team round 1)

Both BLOCKs were real. I reproduced each myself before touching anything —
the red-team had no shell, so its findings arrived as high-confidence claims,
and the manager's own reproductions are independent of mine.

## B1 — the shipped launch command was a silent no-op. Fixed.

**Reproduced** (`bash -c` simulating the flatten-and-reparse `ssh` + the remote
`$SHELL -c` perform):

```
$ bash -c "bash -lc exec pixi --version"   ; echo rc=$?   # (no output) rc=0
$ bash -c "bash -lc 'exec pixi --version'" ; echo rc=$?   # pixi 0.76.1  rc=0
$ bash -c 'bash -lc echo A B C'            ; echo rc=$?   # (no output) rc=0
```

`ssh` appends its arguments "separated by spaces, before it is sent to the
server to be executed" (`ssh(1)`) and never re-quotes; `bash -c` takes only
the **next word** as its command string and assigns the rest to `$0`, `$1`, ….
So the remote ran a bare `exec` — a no-op — and exited **0 having started
nothing**. The worst possible failure for this PR: green, silent, no tools.
The manager is right that the shape is pre-existing (the old string had the
same disease), and equally right that we own the line now.

**Fix:** the whole remote command is now a **single `args` element** carrying
its own quoting —
`"bash -lc 'exec pixi run --frozen --manifest-path <repo>/pixi.toml <repo>/scripts/robot-mcp-launch.sh'"`.
The login shell is **kept** (I cannot verify from here that `pixi` resolves on
a non-interactive `ssh` command on the Pi→laptop hop, and `-lc` is what makes
`PATH` right), so the quoting is embedded rather than the wrapper dropped.

**Verified the way the manager asked — by simulating the flatten, not by
running a locally-quoted variant:**

```
remote="bash -lc 'exec pixi run --frozen --manifest-path $PWD/pixi.toml $PWD/scripts/robot-mcp-launch.sh'"
printf '{"jsonrpc":"2.0","id":1,"method":"initialize",...}\n' | bash -c "$remote"
  → rc=0, exactly 1 line on stdout (the initialize result), pixi's manifest warning on stderr
old="bash -lc exec pixi run --frozen --manifest-path $PWD/pixi.toml $PWD/scripts/robot-mcp-launch.sh"
printf 'x\n' | bash -c "$old"
  → rc=0, 0 bytes of output          # the bug, reproduced against the same launcher
```

**And it has a test now** (the red-team's suggestion, sharpened):
`test_the_flattened_remote_command_is_one_the_remote_shell_can_run` builds the
flattened remote string the way `ssh` builds it (`remote_command()`: everything
after the first non-option argument, space-joined), `shlex.split`s it, and
asserts `['bash', '-lc', <one command string>]` with **nothing after the command
string** (anything there becomes `$0`, `$1`, … and never executes), then splits
the inner string and asserts it `exec`s and ends at the launcher. Confirmed to
bite: restoring the three-element `["bash", "-lc", "exec …"]` form makes it fail
(along with the N2 test), 2 failed / 15 passed.

### The N+1 enumeration (every place a launch command is spelled out)

The heuristic paid: **there was an N+1th the report did not name.**

| # | place | state |
|---|---|---|
| 1 | `src/robot_brain/robot_brain/openclaw/openclaw.robot.json:8-12` — the shipped argv | **was broken → fixed** |
| 2 | `scripts/robot-mcp-launch.sh:19-21` — the header comment showing the deployed command | **N+1th, found by this enumeration: it showed `ssh -T laptop bash -lc 'exec …'`, which is the *same bug* even typed by hand at a terminal (the local shell quotes the string into one argv element, then `ssh` flattens it again). Fixed, with the reason spelled out.** |
| 3 | `src/robot_brain/README.md:121-125` — step 4's manual probe | was a **third** shape (`ssh -T laptop 'pixi run …'`, no login shell) → rewritten to the shipped shape, with the quoting explained |
| 4 | `src/robot_brain/README.md:130-138` — the "what *has* been checked" claim | **was false** → now states exactly what was simulated (`bash -c "<flattened>"`) and that the `ssh` hop and the remote login `PATH` remain unverified |
| 5 | `src/robot_brain/README.md:107-115` — step 3, the two hand-edited paths | accurate; now also pinned by a test (N2) |
| 6 | `src/robot_mcp/README.md:106-118` — the local one-liner (twice) | correct (no `ssh`, no shell in between); verified by direct execution and by the boot-smoke |
| 7 | `src/robot_mcp/README.md:128-141` — the MCP client JSON | correct: `command: "pixi"` + argv array, spawned directly with no shell, so no quoting question arises |
| 8 | `src/robot_mcp/README.md:164` — the `--world-state` example | correct (launcher forwards arguments; covered by a test) |
| 9 | `src/robot_brain/test/test_openclaw_config.py` | now asserts the flattened form (B1) and the one-checkout invariant (N2) |
| 10 | `docs/design/decisions.md` D24 | prose only, no command; gained an accepted-gaps bullet (N9) |
| 11 | `docs/features/i56-ops-hardening/status.md` R1 | **still shows the broken shape** — it is the manager's file and ephemeral (deleted at merge), so I did not edit it. Flagging rather than touching. |

One correction to the dispatch: the false "was checked" claim is in
**`src/robot_brain/README.md`**, not `src/robot_mcp/README.md` (the latter makes
no verification claim at all — its commands are local and are the ones the
boot-smoke actually runs). Fixed where it lives.

## B2 — the new gate tests were not ratchet-protected. Fixed.

The report and the manager are right and `context.md` §2 was wrong: the guard's
own docstring says to re-cut on **additions**, and a floor 15 below the actual
count protects nothing that this PR added.

```
python scripts/check_test_integrity.py --update-baseline
  baseline _workspace_tooling: 111 -> 126
  baseline robot_brain: 48 -> 50
```

Exactly two entries moved (`_workspace_tooling` +15: 7 boot-smoke + 8
provisioning; `robot_brain` +2: the two new launch-command tests). Every other
package is byte-identical — checked in the diff.

**Verified the floor now bites:** moving both new test files out of
`scripts/tests/` and running `pixi run test` →

```
_workspace_tooling  114  …  111  -15  below-baseline
FAIL _workspace_tooling: 111 non-linter tests, 15 below the baseline of 126 …
FAILED stages: test integrity audit          EXIT=1
```

Before this change that deletion left the run green.

## NOTEs addressed

- **N1** — the negative assertion now runs over `json.dumps(server())`, so a
  hand-written `PYTHONPATH` reappearing in the server entry's `env` block is
  caught too. I did **not** drop `"env": {}` from the fragment: it is shipped,
  validated and unrelated to this issue, and changing how a client spawns the
  server is not a change to make on a hunch in a fix round.
- **N2** — new test: the `--manifest-path` argument and the launcher path must
  name one checkout (`launcher.parent.parent == manifest.parent`).
- **N4** — a dangling symlink is now reported as "is a broken symlink"
  (`exists()` follows links; `is_symlink()` does not), with a test.
- **N5** — `written_repository_root` now always creates `src/`, so the test
  exercises "empty `src/`" as its name claims; renamed to
  `test_a_root_with_an_empty_source_tree_refuses_to_launch`. The "directory
  with no manifest" case was already covered (`papers/`).
- **N6** — the control test now spawns `python` from `PATH` with no `cwd=` pin,
  exactly as the launcher's child is spawned, so it cannot drift away from what
  it controls.
- **N7** — the stub interpreter prints one argument per line
  (`printf '%s\n' "$@"`) and the forwarding test passes `--world-state
  '/tmp/a world.json'`, so a lost quote is now visible.
- **N8** — `run-feature.md` step 7 gained: "**The scoped pass is part of that
  round, not another one.**"
- **N9** — D24 corrected in both places: the `src/` enumeration now says what
  actually changed in `test_openclaw_config.py` (four edits, two tests, not
  "the test"), and a fourth bullet records the **accepted gaps** in D23's style
  — the `ssh` hop, the two absolute paths and the `pixi run` prefix are not
  executed by any test; what is asserted instead is their *shape*.
- **N3** — left as-is per the manager's ruling (R7 stands). Not documented in
  the guard's docstring either, since the manager kept it as a deliberate
  consequence rather than an accepted gap; say the word if you want it written
  down.
- **N10** — triage agreed; the `command -v python || die` suggestion was **not**
  taken (it changes launcher runtime behavior and was not asked for; `python:
  command not found` on stderr with rc 127 is already legible).

## Test result after the fix round (honest)

`pixi run test` → **EXIT=0**, **694 tests, 0 errors, 0 failures, 0 skipped**,
`AUDIT PASSED`, `All stages passed.` Per-package after the re-cut:
`_workspace_tooling` 126 (+0), `robot_brain` 50 (+0), everything else +0.

Nothing is red and nothing is skipped. What remains **unverified by anyone**,
and is now written down in D24 rather than assumed: the `ssh` hop itself and
the remote login shell's `PATH` — only the Pi can exercise those, and the
README's step 4 probe is the way to do it.

---

# Fix round 1b (scoped red-team pass on the fix commit)

Two one-liners, both taken; NOTE 3 declined per the manager.

- **Absolute paths (the real hole).** `test_the_two_absolute_paths_in_the_command_name_one_checkout`
  compared the two paths to each other but never to `/`. A **fully relative**
  fragment is internally consistent and passes every other launch-command
  assertion while being unrunnable — the remote shell starts in `$HOME`, not in
  the checkout. Added `assert manifest.is_absolute() and launcher.is_absolute()`
  with the reason in the docstring. Proved the hole was real: with the fragment
  rewritten to `--manifest-path pixi.toml scripts/robot-mcp-launch.sh`, the run
  is **1 failed, 16 passed** — the new assertion is the only thing that catches
  it.
- **`remote_command()`'s destination detection.** Guarded rather than
  generalised: `VALUELESS_SSH_OPTIONS` (`-T -t -q -n -4 -6`) plus one assert
  that says *the helper needs teaching*, so a future `-o BatchMode=yes` blames
  the right thing instead of the launch command. Verified: adding
  `-o BatchMode=yes` fails with `['-T', '-o']: an ssh option that takes a
  value … teach it the new option rather than reading the failure below as a
  broken launch command`. No ssh argument parser was built.
- **NOTE 3 declined** (a *directory* at the CLI path reports "is not
  executable"): implausible state, and the message still points at the one
  command that fixes it.

**The `-lc` gap is now closed on the laptop side**, and I re-checked it myself
with a sharper simulation than the report's: `env -i` alone has **no `HOME`**,
so `~/.profile` is never sourced and even the login shell finds nothing — which
would have been a misleading verification. With the variables `sshd` actually
sets:

```
env -i HOME=$HOME USER=$USER SHELL=/bin/bash bash -c  'command -v pixi'  → (not found)
env -i HOME=$HOME USER=$USER SHELL=/bin/bash bash -lc 'command -v pixi'  → /home/sisyphus/.pixi/bin/pixi
```

So `-lc` is load-bearing and checked, not assumed. D24's accepted-gaps bullet
and `src/robot_brain/README.md` now say exactly that, and narrow the remaining
gap to the `ssh` hop itself and the **Pi's** login shell.

**Suite after 1b:** `pixi run test` → EXIT=0, **694 tests, 0 errors, 0
failures, 0 skipped**, `AUDIT PASSED`, `All stages passed`; every package `+0`
against the re-cut baseline (no test was added or removed in this round, only
assertions strengthened).
