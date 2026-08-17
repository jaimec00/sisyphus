# Sisyphus

An autonomous household-chore robot: an LLM "brain" driving a mobile manipulator
(holonomic base, extendable column, two arms), built on **ROS 2 Jazzy** and
developed **sim-first** in MuJoCo.

> **Early-stage.** This repo currently holds the architecture, the development
> workflow, and the package skeleton — not yet a working robot. See
> [`docs/design/spec.md`](docs/design/spec.md) for what the robot currently is,
> [`docs/design/decisions.md`](docs/design/decisions.md) for the decision log
> (the source of truth), and
> [`docs/design/PROJECT.md`](docs/design/PROJECT.md) for the goal and open
> questions.

## Layout
- `src/robot_*` — ROS 2 packages: `robot_brain`, `robot_skills`, `robot_safety`,
  `robot_backends`, `robot_perception`, `robot_description`, `robot_bringup`.
- `docs/design/` — architecture + decisions (source of truth).
- `docs/features/` — per-feature briefs and reports.
- `AGENTS.md` — canonical agent-rules file (roles, loop, invariants, merge policy).

## Getting started (laptop, headless-friendly)
Requires [pixi](https://pixi.sh). ROS 2 Jazzy is provided via RoboStack.

```bash
pixi install
pixi run build   # colcon build --symlink-install
pixi run test    # colcon test + results + zero-test guard
```

`pixi run test` runs the workspace suite through
[`scripts/check_test_integrity.py`](scripts/check_test_integrity.py), which
prints a per-package table of collected tests and **fails** if any package
produced no result file, collected zero tests, or skipped every test it
collected — `colcon test` alone reports all three as green. Every package
whose `package.xml` this repo tracks is audited (`COLCON_IGNORE` grants no
exemption); packages imported into `src/` from elsewhere are listed but not
required to have tests. It also **ratchets** each package against the count of
tests it ran last time (`scripts/test_baseline.json`), so a suite cannot
quietly shrink — by deletion or by `@pytest.mark.skip`. That floor keeps
itself: a green run raises it and leaves the updated file to be committed with
the tests that grew it, while *lowering* one is deliberate
(`ALLOW_TEST_DECREASE=1 pixi run test`). Use `pixi run test-audit` to re-read
the last run's results, with their age, without re-running anything — it never
writes.

## Development
Built by a hierarchy of coding agents (context → implement → red-team → test →
squash-merge). See [`AGENTS.md`](AGENTS.md) for the canonical agent rules and the
development loop.

## License
MIT — see [`LICENSE`](LICENSE).
