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
  `test_a_root_with_no_packages_refuses_to_launch` — cheap unit tests of
  discovery (R8's "welcome in addition"). They make discovery observable by
  shadowing `python` on `PATH` with a stub that prints `$PYTHONPATH` and
  `"$@"`, so nothing boots.
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
`exec pixi run --frozen --manifest-path <repo>/pixi.toml <repo>/scripts/robot-mcp-launch.sh`
— no `PYTHONPATH`, no package names. `REQUIRED_PACKAGES` is deleted;
`test_the_launch_command_carries_every_package_the_server_needs` became
`test_the_launch_command_leaves_the_package_list_to_the_launcher`, asserting the
*negative* (`'PYTHONPATH' not in command`, `'/src/' not in command`), and
`test_the_launch_command_starts_this_repos_server_over_stdio` now asserts the
launcher — **and that the launcher it names exists in this checkout**, so
renaming or moving the script fails here rather than on the Pi. Test count
unchanged (rewrites, not removals): `robot_brain` stayed at 48 non-linter, so
no baseline re-cut was needed and `scripts/test_baseline.json` is untouched.

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
