# Red-team — #56 Ops hardening (close the CI-green-but-deploy-broken class)

Branch `feat/i56-ops-hardening-close-the-ci-green-but-dep`, 6 commits on
`origin/main` @ `e13c3e6`. Reviewed against issue #56's acceptance criteria,
`CLAUDE.md`'s invariants + rubric, and manager rulings R1–R11.

## Methodology, stated up front (this qualifies every finding below)

**I had no Bash tool in this session.** The dispatch asked me to disprove the
rulings *empirically — run the code, don't reason about it*; my tool set was
`Read`/`Grep`/`Glob` only, so **I could not execute a single command**: no
sabotage runs, no `pixi run test`, no `git diff origin/main..HEAD`. Everything
below is grounded in files I read line by line plus filesystem inspection via
`Glob`. Where a finding rests on documented external behavior I could not
exercise, it is labelled **UNEXECUTED** and carries the exact command that
settles it. Treat the two BLOCKs as *high-confidence claims with a one-command
falsification test*, not as observed failures.

Two consequences the manager should route around:

- **C1's sabotage attack (re-introduce the four-package filter / empty
  discovery and re-run the smoke) was not performed by me.** I attacked it
  statically instead, including the ambient-leak half, which *is* settleable by
  inspection (see "What I could verify" below).
- **Acceptance criterion 4 ("no `src/` behavior change; the seam untouched")
  could not be verified** — I cannot run `git diff origin/main..HEAD -- src/`.
  Targeted `Grep` over `src/` found no leftover `PYTHONPATH` package list and no
  reference to the launcher outside the fragment, the two READMEs and
  `test_openclaw_config.py`, which is consistent with the claim but is not
  proof. **Someone with a shell must run that diff before merge.**

---

## What I could verify (the load-bearing negative results)

These matter because they are the ways this feature could have been theatre,
and they are closed:

1. **No ambient import path can make the smoke test vacuous.**
   `Glob '.pixi/envs/*/lib/python3*/site-packages/*.pth'` returns exactly
   `coloredlogs.pth`, `distutils-precedence.pth`, `a1_coverage.pth` — none adds
   the workspace. `Glob '.pixi/envs/*/lib/python3*/site-packages/robot_*'`
   returns **nothing**: there is no installed or develop-mode copy of the
   workspace packages in the pixi env. `Glob 'install/**/easy-install.pth'`
   returns nothing. The only route into the child is `PYTHONPATH`, which
   `undiscovered_environment()` (`scripts/tests/test_boot_smoke.py:87-88`)
   drops, and colcon's `--symlink-install` overlay is reachable *only* through
   `PYTHONPATH`. So even after `pixi run build` + `source install/setup.bash`,
   the child gets nothing it did not discover.
2. **cwd cannot leak either.** `run_tooling_tests` runs pytest with
   `cwd=str(repo_root)` (`scripts/check_test_integrity.py:715`), and
   `StdioServerParameters(command=...)` passes `cwd=None`, so the child inherits
   the repo root — which contains no top-level `robot_mcp`/`robot_world`
   package. (`src/` would give namespace packages with no `__main__`, so even
   `cd src` does not rescue the child.)
3. **`test_the_stripped_environment_really_hands_the_child_nothing`
   (`:257-270`) is a genuine control**, and R5's non-negotiable was honored:
   `mcp_fixtures.clean_environment()` is *not* reused. Given (1) and (2), the
   positive handshake really does depend on the launcher's discovery.
4. **The bites test's premise is real:** `src/robot_mcp/robot_mcp/server.py:73`
   is an unconditional module-load `from robot_world import FileWorldStore`, so
   removing `robot_world` from the source tree is a boot failure, not a degraded
   feature.
5. **The `readlink -f` sabotage really would go red, and the implementer's
   "4 failed, 3 passed" is self-consistent** (C2). With `readlink -f` the fake
   root resolves back to the real checkout, so
   `test_every_package_with_a_manifest_lands_on_the_path`,
   `test_an_inherited_pythonpath_is_appended_not_clobbered`,
   `test_a_root_with_no_packages_refuses_to_launch` and
   `test_dropping_a_required_package_breaks_the_boot` all break, while
   `test_the_servers_own_arguments_are_forwarded`, the control and the positive
   handshake still pass — exactly 4/3. The symlink-instead-of-copy deviation
   from R8 is sound: `cd "$(dirname "${BASH_SOURCE[0]}")/.."` resolves the
   *directory* physically and the symlink is the *file*, so the fake root is
   preserved under direct exec, `bash <path>` and
   `StdioServerParameters(command=...)` alike.
6. **The bash is clean under the attacks in C3.** `${#discovered[@]}` is safe
   under `set -u` on an empty array; `"${discovered[*]}"` is only reached after
   the count guard; unset `nullglob` makes the glob yield the literal path,
   which `[ -f "$manifest" ] || continue` correctly rejects (so a missing/empty
   `src/` dies loudly); `${PYTHONPATH:+:$PYTHONPATH}` is one of the forms `set
   -u` exempts; paths with spaces survive (every expansion is quoted, `IFS=:`
   join is subshelled); order really is discovered-first, matching the comment
   and `:225`. A repo path containing `:` would corrupt `PYTHONPATH`, but that
   is inherent to `PYTHONPATH` and not worth code.
7. **The ratchet semantics are as claimed:** `audit_package` fails only on
   `non_linter < baseline` (`scripts/check_test_integrity.py:487`). See BLOCK 2
   for why "unchanged baseline" is nonetheless the wrong call here.

---

## BLOCK

### B1 — The repointed launch command in the shipped fragment cannot start the server: `ssh` flattens `args` and `bash -lc` swallows it

**Where:** `src/robot_brain/robot_brain/openclaw/openclaw.robot.json:8-14`;
claim repeated in `src/robot_brain/README.md:121-138`; asserted (too weakly) by
`src/robot_brain/test/test_openclaw_config.py:298-311` and `:313-321`.

The fragment ships:

```json
"command": "ssh",
"args": ["-T", "laptop", "bash", "-lc",
         "exec pixi run --frozen --manifest-path /home/.../pixi.toml /home/.../scripts/robot-mcp-launch.sh"]
```

An MCP client spawns this as a real argv (no shell), so `ssh` receives five
arguments. `ssh(1)` is explicit: *"If supplied, the arguments will be appended
to the command, separated by spaces, before it is sent to the server to be
executed."* **`ssh` does not re-quote.** The remote login shell therefore
receives the single string

```
bash -lc exec pixi run --frozen --manifest-path /home/.../pixi.toml /home/.../scripts/robot-mcp-launch.sh
```

and word-splits it. `bash -c` takes the **next word** as its command string, so
the command string is `exec`, and `pixi`, `run`, `--frozen`, … become `$0`,
`$1`, …. `exec` with no command and no redirections is a documented no-op: the
shell returns 0 and exits.

**Failure scenario:** Sisyphus does the post-merge Pi repoint from this
template (which is R3's entire stated justification for touching `src/` at
all). OpenClaw spawns the server, the pipe closes immediately with exit 0, no
JSON-RPC frame is ever emitted, and the symptom is *"the agent has no tools"* —
the exact symptom `src/robot_brain/README.md:114` warns about, with no
`ModuleNotFoundError` and no traceback to grep for. This is the #56 bug class
reproduced in the artifact whose repointing was supposed to close it.

**The verification in `implementation.md:167-171` does not cover this.** What
was run is

```
printf '{...}' | bash -lc "exec pixi run --frozen --manifest-path $PWD/pixi.toml $PWD/scripts/robot-mcp-launch.sh"
```

— the **quoted local** form. The fragment provides no quoting, and the repo's
own manual-check recipe (`src/robot_brain/README.md:123-124`) uses a *third*
shape, `ssh -T laptop 'pixi run …'` with single quotes and **no `bash -lc`**.
Three shapes are documented and only the untested one ships. `README.md:130-134`
then claims *"the exact command the fragment ships, run through `bash -lc`
(login shell and all), puts a single JSON-RPC frame on stdout"* — that sentence
is false as written: what was checked is not the command the fragment ships.
In a PR about documented claims no check verifies, that is the finding.

**UNEXECUTED.** Two commands settle it in seconds:

```bash
# half 1 — bash -lc with an unquoted command string does nothing:
bash -lc exec pixi run --version ; echo "flattened rc=$?"   # expect: no output, rc=0
bash -lc 'exec pixi run --version' ; echo "quoted rc=$?"    # expect: a version, rc=0
# half 2 — ssh flattens argv (any reachable host, incl. localhost):
ssh -T localhost bash -lc 'echo HELLO'                      # expect: nothing
ssh -T localhost "bash -lc 'echo HELLO'"                    # expect: HELLO
```

If half 1 prints a version, I am wrong — downgrade this to a NOTE.

**Fix direction:** make the last `args` element a single, remote-shell-parseable
string, i.e. `["-T", "laptop", "bash -lc 'exec pixi run --frozen
--manifest-path <repo>/pixi.toml <repo>/scripts/robot-mcp-launch.sh'"]` (or drop
`bash -lc` entirely and let ssh's own shell run it, as `README.md:123` already
does), then re-run the pipe probe through an actual `ssh` hop. **And give it a
test:** `launch_command()` (`test_openclaw_config.py:76-78`) already computes
the flattened string — it joins `command` + `args` with single spaces, which is
*precisely what `ssh` does*. Today the tests only substring-match on it; the join
that exposes the bug is being used to hide it. Assert instead that
`shlex.split(<the remote portion>)` yields an argv that actually invokes the
launcher (e.g. `shlex.split(' '.join(args[2:]))` must end at the launcher path,
not at `exec`). Fix the README's verification claim to say what was actually
run.

*If the manager rules the ssh quoting out of scope as pre-existing (I could not
`git diff` to establish whether the `bash`/`-lc` split into separate array
elements is new in this PR): the minimum acceptable action is to delete the
"the exact command the fragment ships … has been checked" claim from
`src/robot_brain/README.md` and record the untested hop as an accepted gap in
D24, the way D23 records its accepted gaps.*

### B2 — The gate this feature adds is not protected by the ratchet: both new test files can be deleted without `pixi run test` noticing

**Where:** `scripts/test_baseline.json:4` (`_workspace_tooling: 111`, unchanged),
against `scripts/check_test_integrity.py:487` and its own instruction at `:26`.

`scripts/tests/test_boot_smoke.py` adds **7** tests and
`scripts/tests/test_provisioning.py` adds **7** — 14, which is exactly the
`+14` `implementation.md:176` reports (111 → 125). The ratchet fails only when
`non_linter < baseline` (`:487`). So **deleting both new files outright leaves
`_workspace_tooling` at exactly 111 = the baseline, and the run stays green.**
Every other guard in `audit_package` is per-*package*, not per-file: the package
still has results, still has non-zero tests, still has non-linter tests.

**Failure scenario:** six months from now someone "temporarily" comments out
`scripts/tests/test_boot_smoke.py` because a slow handshake annoys them, or a
`testpaths`/collection change silently stops collecting it. `pixi run test` is
green, `pixi run boot-smoke` is not run by anything in the loop, GitHub CI only
checks docs-clean — and the deploy path is unguarded again, which is the entire
premise of #56. The docstring of the guard itself (`:18-20`) names exactly this
failure ("drop from 59 tests to 3 … a stray `testpaths` edit").

`implementation.md:105-106` justifies the untouched baseline as "additions never
trip the ratchet". True, and beside the point: the guard's own instruction at
`scripts/check_test_integrity.py:26` is *"Update the baseline (and commit it)
whenever tests are legitimately **added** or removed"*, and `test_baseline.json`'s
own comment calls it "the floor". A floor 14 below the actual count is not a
floor for the 14 things this PR exists to protect.

**Fix direction:** `python scripts/check_test_integrity.py --update-baseline`
from the green run and commit `scripts/test_baseline.json` (expected:
`_workspace_tooling: 125`, everything else unchanged — the helper refuses to
write from a non-green run, so this is safe). **UNEXECUTED** — I could not run
it; the count 125 is from `implementation.md`, and the command re-derives it.

---

## NOTE

### N1 — The negative assertion only sees `command` + `args`, not the server entry's `env`
`test_openclaw_config.py:339-342` checks `'PYTHONPATH' not in command` where
`launch_command()` (`:76-78`) joins only `command` and `args`. The fragment
still carries `"env": {}` (`openclaw.robot.json:15`), and an MCP client config's
`env` block is the *natural* place for someone to re-add a hand-written package
list — the test whose whole purpose is to ban hand lists would not see it.
Severity is only NOTE because the launcher discovers regardless, so such a list
would be inert rather than wrong. Fix: assert over `json.dumps(server())`
instead of `launch_command()`. While there: the `robot_mcp` README's client
example (`README.md:130-147`) deliberately ships **no** `env` block; making the
fragment agree (drop `"env": {}`) also removes the risk of a client treating an
explicit empty `env` as "spawn with an empty environment".

### N2 — Nothing checks the fragment's two hard-coded absolute paths against each other
`--manifest-path /home/.../main/pixi.toml` and
`/home/.../main/scripts/robot-mcp-launch.sh` are independent literals
(`openclaw.robot.json:13`). If a future edit updates one and not the other, the
deploy runs checkout **B**'s launcher inside checkout **A**'s environment — a
new drift instance of the same class, and `src/robot_brain/README.md:107-114`
now explicitly instructs a human to hand-edit both. One assertion in
`test_openclaw_config.py` (the two paths share a parent; the `--manifest-path`
argument ends in `pixi.toml`) costs two lines.

### N3 — The provisioning guard fires on narrowed runs that do not need OpenClaw
`pixi.toml:44` makes `check-provisioning` a `depends-on` of `test`, so
`pixi run test --packages-select robot_safety` — the workflow
`.claude/agents/test-runner.md:9` explicitly endorses ("narrow to the feature's
packages when appropriate") — now hard-fails on a worktree with no `node/`, for
a run that would never have touched `robot_brain`. `depends-on` cannot see the
task's trailing arguments, so this is structural rather than fixable cheaply.
Record it in `check_provisioning.py`'s docstring as an accepted consequence
(the remediation is one command, and R7's hard-fail-no-skip ruling is right).

### N4 — A dangling `openclaw` symlink is reported as "is missing"
`check_provisioning.py:81` uses `binary.exists()`, which follows symlinks, so a
`node/node_modules/.bin/openclaw` symlink whose target was removed (a
half-cleaned `node_modules`) yields *"the OpenClaw CLI is missing: <path>"* —
while `ls` shows the path right there. `os.path.lexists()` (or
`binary.is_symlink()`) lets the message say "is a broken symlink", which is the
difference between one command and ten minutes. Same remediation either way,
hence NOTE. (The provisioned case is fine: `Path.is_file()` + `os.access(...,
X_OK)` both follow the symlink to `../openclaw/openclaw.mjs`.)

### N5 — `test_a_root_with_no_packages_refuses_to_launch` tests a root with no `src/` at all
`written_repository_root` (`test_boot_smoke.py:120-134`) only creates
`root/src/<pkg>` inside the loop, so with `packages=[]` the `src/` directory is
never created — the test at `:239-254` exercises "missing `src/`", not "empty
`src/`", which is what its name and docstring claim. Both die identically today,
so this is documentation drift rather than a hole; `(root / 'src').mkdir()`
unconditionally (as `fake_repository_root:105` already does) makes the test
match its name, and a second case covering a `src/` holding a directory with no
`package.xml` would pin the `[ -f ]` guard.

### N6 — The control test does not control quite the environment it guards
`test_the_stripped_environment_really_hands_the_child_nothing` (`:264-267`)
pins `cwd=str(REPO_ROOT)` and uses `sys.executable`, while the child actually
under test inherits the pytest process's cwd and resolves `python` from `PATH`.
Under `pixi run test` these coincide (verified: `run_tooling_tests` sets
`cwd=repo_root`), so nothing is wrong today — but the control is the one test
that must not drift from the thing it controls. Drop the `cwd=` pin (inherit,
like the child does) and spawn `'python'` via `PATH` with `shell=False`.

### N7 — `test_the_servers_own_arguments_are_forwarded` cannot see quoting
The stub does `echo "$@"` (`:69`) and the test compares the joined line
(`:235-236`), so one argument containing a space is indistinguishable from two
arguments. The launcher's `"$@"` (`robot-mcp-launch.sh:59`) is correct, so this
is only a loose assertion: `printf '%s\n' "$@"` in the stub plus a
`--world-state '/tmp/a b.json'` case makes it exact.

### N8 — `run-feature.md` step 7 does not say whether the scoped fix-pass counts against the 2-round budget
`.claude/commands/run-feature.md:55-62` introduces "red-team the fix itself, a
second pass scoped to just the fix diff" and, three lines later, "Max **2**
red-team↔fix rounds". A manager reading this cannot tell whether the scoped pass
*is* a round, is free, or ends the budget. Task 5a/5b are otherwise landed
exactly as R9 specifies (falsifiable claim + "disprove that claim empirically —
run the code, don't reason about it"; the *N places → N+1th* heuristic verbatim
in spirit), and task 4 is in step 9 pre-"ready" with the matching one-line
prose in `DEVELOPMENT.md:89-91` per R10, with no duplication between the two
files. One clause fixes this: "the scoped pass is part of that round, not a
third."

### N9 — D24 prose drifts from the diff in two small ways (this is the task-4 dogfood, so it is fair to say so)
`docs/design/decisions.md:67` says the only `src/` edits are "the OpenClaw
fragment's launch string, **the test** that used to assert the hand list (now
asserting its absence), and the two READMEs". In fact `test_openclaw_config.py`
changed in four places: the module docstring (`:7-29`), a new
`LAUNCHER_RELATIVE_PATH` constant (`:42-45`), a new import of `repository_root`
(`:38`) and a **second** test —
`test_the_launch_command_starts_this_repos_server_over_stdio` (`:298-311`), which
is not the hand-list test. Second: D24's fourth bullet states the rule *"anything
hand-typed on a path to production is a second, unsynced source of truth; the
remedy is to derive it or to make the gate run it"* — while the very fragment it
ships still hand-types two absolute paths, an ssh alias, and a `pixi run
--frozen --manifest-path` wrapper that **no test in the gate ever executes** (the
smoke calls the launcher directly, already inside pixi — correctly, per R1).
D24's own claims about the gate are literally true (it does boot *the identical
launcher file*), so this is not an overclaim; it is a missing accepted-gaps
paragraph of the kind D23 sets the precedent for. Format/numbering are correct
(`## 2026-08-12 — …` + `- **D24 — … (closes #56).**`; a second same-date section
already has precedent in the two 2026-08-11 sections).

### N10 — surviving-NOTE triage from `implementation.md` is correct
- *`start-op.sh` never provisions*: correct as a NOTE. `Grep` over `.claude/`
  finds no `pixi run test` in `run-op.md`; only `implementer.md`,
  `test-runner.md`, `run-merge-eval.md` and `settings.json` mention it, none of
  which operational agents drive. And an unprovisioned op worktree was *already*
  red (six `robot_brain` failures) — this PR changes the message, not the color.
- *The Pi config stays broken until Sisyphus's repoint*: correct as a NOTE, but
  see **B1** — the template it will be repointed from needs fixing first.
- *`exec python` from `PATH`*: correct as a NOTE. Failure is `python: command
  not found` on stderr with rc 127 — legible, just not `die`-styled. A
  `command -v python >/dev/null || die "no python on PATH — run me through
  pixi"` line would cost nothing if someone is in the file anyway.

---

## Test adequacy — explicit assessment

**`scripts/tests/test_boot_smoke.py` (7 tests): strong, and not theatre.** The
control test plus the dropped-`PYTHONPATH` child environment are the two things
that could have made this file vacuous, and both are handled deliberately and
documented as load-bearing. The bites test asserts the child's *stderr*, not
merely that something raised — which is the difference between "discovery broke
the boot" and "the server hung", and is the single best judgment call in the
diff. `pytest.raises(Exception)` is broad, but it is paired with the specific
stderr assertion, so a wrong-path/`FileNotFoundError` would still fail the test
(empty errlog). Nothing asserts on its own fixtures; the discovery unit tests
use invented package names so the expected answer is stated in the test rather
than read from the workspace. No stray processes (the `stdio_client` context
manager reaps the child), no mutation of the real `src/` (every path into it is
a symlink, read-only), everything under `tmp_path`. Two real interpreter boots
per run is an acceptable cost for the gate. One residual flake risk I could not
exercise: the bites test reads `errlog` after the async context unwinds and so
depends on the child's stderr having been flushed by then — deterministic in the
"child dies at import" path, less so on the timeout path.

**`scripts/tests/test_provisioning.py` (7 tests): adequate.** No tautologies;
the "provisioned passes and says nothing" baseline is exactly the test that
makes the failure tests mean something; `monkeypatch.setattr(guard, 'CHECKS',
…)` works because `problems()` reads the module global at call time; the
`pixi.toml` wiring test asserts the *shape that decides the behavior*, including
the negative (`test-audit` must stay a plain string). What is **not** covered —
and cannot cheaply be — is that pixi's `depends-on` really short-circuits and
propagates; that rests on the manager's P1 probe alone.

**`src/robot_brain/test/test_openclaw_config.py`:** the inversion (R4) is the
right shape and does bite for the `PYTHONPATH=`/`/src/` forms; the added
`(repository_root() / LAUNCHER_RELATIVE_PATH).is_file()` assertion is a genuine
improvement over the deleted `REQUIRED_PACKAGES` list, and no coverage was lost
in the rewrite. Its two holes are N1 (the `env` block) and, much more seriously,
**B1** (the shape of the command it asserts on is not runnable, and the test's
own `launch_command()` join is the thing that would have exposed it).

**Coverage of the acceptance criteria:** AC1 covered (unit + integration, and
the integration half genuinely depends on discovery); AC2 covered; AC3 present
and actionable, modulo N8; AC4 **unverified by me** — needs
`git diff origin/main..HEAD -- src/`.

## Verdict

Two BLOCKs, both cheap to fix and both settleable by one command each. The
feature is otherwise the real thing rather than theatre: the manager's R5
non-negotiable was honored and I could not find a path by which the smoke test
passes on broken discovery. B1 is the one that matters — the PR closes the
CI-green-but-deploy-broken class everywhere except in the one artifact the
deployment is actually copied from.
