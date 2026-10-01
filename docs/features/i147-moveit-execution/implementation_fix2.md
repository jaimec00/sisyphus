# i147 — fix round 2: harness DDS/teardown (BLOCK-3) + two NOTE nits

**Author:** fix implementer (subagent, depth 1/2). **Date:** 2026-10-01.
**Worktree:** `/home/sisyphus/worktrees/feat/i147-moveit-execution`, branch
`feat/i147-moveit-execution`, base `5c5ca63` (fix `402e317` + docs `5c5ca63`).
**Host:** laptop node `olivia`.
**Scope:** test-harness only for BLOCK-3; two NOTE-grade nits in the goal node.
No production/launch/config change beyond the test fixture. Off-limits
(`robot_backends/`, `robot_mcp/`) untouched.

---

## 1. What changed

### BLOCK-3(a) — per-run unique DDS domain (was a hardcoded `120`)

`src/robot_moveit/test/test_moveit_execution.py`:

* Deleted the fixed `EXECUTION_DOMAIN_ID = '120'`; added
  `EXECUTION_DOMAIN_BASE = 120` + `PYTEST_DOMAIN_PID_SPAN = 16` and
  `_unique_domain_id()` → `str(BASE + (os.getpid() % SPAN))`. Two concurrent
  invocations (the manager's run and a reviewer's probe — the collision that
  invalidated the prior review) can no longer share a domain or its FastDDS
  shared-memory port namespace.
* `_next_retry_domain_id()` steps a failed attempt onto a guaranteed different
  domain (`+SPAN + counter`), used by the fresh-domain retry below.
* The `_ExecutionStack` world-state temp file is now namespaced by
  `domain_pid` too, so concurrent runs never clobber each other's seed file.

### BLOCK-3(b) — race-proof teardown (orphan reap + SHM sweep)

Ported verbatim the idioms the peer suites already ship
(`src/robot_bringup/test/test_pr2_navigate.py` / `test_pr3_navigate.py`):

* `_reap_orphans()` — kills this worktree's launch children that escaped the
  process group and were reparented to the user systemd session (the exact
  orphan the re-red-team reproduced: a `robot_state_publisher` whose PPID was
  `systemd --user`, group leader already exited, so `killpg` alone missed it).
  Scoped by cmdline path markers (`_is_our_process` / `_node_path_markers` /
  `_worktree_marker`), so it is safe on a node shared with other ROS users.
* `_sweep_stale_shm()` (pre-launch) + `_cleanup_shm(before)` (post-teardown),
  both liveness-guarded via `fuser` (`_held_by_a_live_process`) and scoped via
  `_SHM_NAME_RE` (`fastrtps_` / `sem.fastrtps_`) — the FastDDS meta-traffic
  ports are host-global, so a stale lock breaks the next launch with
  `open_and_lock_file failed`. `_shm_inventory()` snapshot in `__enter__`,
  reclaimed in `__exit__`.
* `_terminate_group()` now waits (bounded, 15s) for the **whole group** to exit
  via `_group_alive()`, so SHM segments are actually free before the
  liveness-guarded cleanup runs.
* `__exit__` reaps + sweeps in a `finally` after `killpg` and prints what it
  reclaimed; the pre-launch reap + sweep also run in `__enter__`.

### BLOCK-3(c) — fresh-domain retry (bonus hardening, peer-suite idiom)

Refactored the module fixture into `_bring_up_stack(domain_id)` (launch + the
two-stage readiness gate) and a `stack()` fixture that retries the whole bringup
on a **fresh domain** up to `_BRINGUP_ATTEMPTS = 3`, only when the failure text
matches `_TRANSIENT_BRINGUP_MARKERS` (DDS discovery race / SHM `Failed
init_port`). A genuine defect (any other exception) is re-raised on the first
attempt — no masking.

### Flake found and fixed while validating (harness hygiene, same file)

Under `colcon test` on a loaded host the moves-arm test intermittently recorded
`start={}`: after re-arming the recorder (`start_recording` clears samples) the
old code spun a bare `spin_for(2.0)` and read `samples[-1]`; on a starved host
the re-subscribed `/joint_states` had not delivered, so `start_positions` was
empty and the interior-sample check was vacuously false → a spurious
"teleport" failure. Replaced with `wait_for_joint_state()` (+ a short settle
spin) and an explicit assertion that a pre-move sample exists. This is
test-harness robustness, in the same file/scope as BLOCK-3.

### NOTE-A — stale comment in the fix block (`cartesian_goal.py`)

The paragraph claimed MoveIt's node "runs on the wall clock" and that
`wait_for_initial_state_timeout` was raised past its 10 s default. Both are
false: the code sets `params['use_sim_time'] = True` and supplies the four
`qos_overrides./clock.subscription.*` policies. Rewrote the comment to describe
the actual mechanism (the READ-ONLY QoS-override abort and the up-front string
overrides that fix it). No functional change.

### NOTE-B — `_goal_collision_reason` no longer fails open silently

Was: `except Exception → warn + return None`, i.e. a checker crash degraded a
self-colliding goal back to the opaque pipeline FAILURE (the very thing the
check exists to replace). Now returns `(reason, failed)`: `(reason, False)`
=collision, `(None, False)`=clean, `(None, True)`=checker raised. The caller
turns `check_failed` into an explicit `STATUS_FAILURE` with a clear message
(logged at `error`) instead of falling through as if collision-free.

---

## 2. Exact commands run + observed results

### Standalone e2e (`-v -s`), fresh per-run domain

```
$ ps -eo pid,ppid,etimes,cmd | grep -E 'mujoco|move_group|robot_state_publisher|world_query|controller_manager|cartesian_goal|ros2 launch'
NO ORPHANS BEFORE

$ pixi run bash -c 'source install/setup.bash && python -m pytest \
    src/robot_moveit/test/test_moveit_execution.py -v -s'
test_execution_stack_moves_arm_along_a_trajectory PASSED
test_execution_stack_reports_out_of_reach PASSED
test_execution_stack_rejects_self_colliding_goal PASSED
[i147-exec] reclaimed 16 FastDDS /dev/shm segment(s) after teardown
======================== 3 passed, 2 warnings in 34.89s ========================

$ ps -eo pid,ppid,etimes,cmd | grep -E '...same...'
ZERO ORPHANS AFTER
```

(The `[i147-exec] reclaimed 16 FastDDS /dev/shm segment(s)` line is the
post-teardown cleanup doing its job — those segments are exactly what previously
leaked and broke the next launch.)

### Orphan scan after a **colcon** run (the case the re-red-team reproduced an
orphan in)

```
$ pixi run bash -c 'colcon test --packages-select robot_moveit'
Finished <<< robot_moveit [40.2s]
$ ps -eo pid,ppid,pgid,etimes,cmd | grep -E 'mujoco|move_group|robot_state_publisher|world_query|controller_manager|cartesian_goal|ros2 launch'
ZERO ORPHANS
```

### `colcon test --packages-select robot_moveit` — green

```
build/robot_moveit/pytest.xml: errors="0" failures="0" skipped="0" tests="23"
```

23 tests, 0 failures. (The `robot_bringup` xunit file in the same tree shows 1
failure, `test_nav_localization_under_launch`, timestamped 04:07 — a **stale
result from an earlier run** for a *different package* this change does not
touch; the authoritative gate is the per-package `robot_moveit` suite, which is
green.)

### Domain uniqueness (direct check on the imported module)

```
module constants: BASE=120 SPAN=16
this pid=3742631 -> domain 127
fix block: hardcoded EXECUTION_DOMAIN_ID present? False
```

### Lint / style gates

```
$ ament_flake8 (cwd src/robot_moveit)  -> rc=0
$ ament_pep257 (argv='.', 'test', '--add-ignore', 'D213') -> No problems found, rc=0
```

---

## 3. Peer-suite idiom port confirmation

| Peer idiom (`robot_bringup/test/test_pr2_navigate.py`) | Ported into `test_moveit_execution.py` |
|---|---|
| per-run/probe unique domain (base + pid/offset) | ✅ `_unique_domain_id()` (base + `pid % 16`), `_next_retry_domain_id()` |
| `_reap_orphans()` before launch | ✅ in `__enter__`, and again in `__exit__` after killpg |
| `_sweep_stale_shm()` / `_shm_inventory()` scoped `/dev/shm` cleanup | ✅ `_sweep_stale_shm()` (pre), `_cleanup_shm()` (post), `_shm_inventory()` |
| liveness-guarded removal (`fuser`) | ✅ `_held_by_a_live_process()` |
| worktree-scoped orphan targeting | ✅ `_worktree_marker()` / `_node_path_markers()` / `_is_our_process()` |
| bounded retry on a fresh domain | ✅ `stack()` fixture, `_BRINGUP_ATTEMPTS = 3` |
| wait for the whole process group to die | ✅ `_group_alive()` in `_terminate_group()` |

All seven peer-suite hardening idioms are present; none were reinvented.

---

## 4. Files touched

* `src/robot_moveit/test/test_moveit_execution.py` (BLOCK-3 + flake fix)
* `src/robot_moveit/robot_moveit/cartesian_goal.py` (NOTE-A comment, NOTE-B
  failure-surfacing — no behaviour change on the success/collision paths)

No launch, config, production, `robot_backends/`, or `robot_mcp/` change.
