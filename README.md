# Sisyphus

An autonomous household-chore robot: an LLM "brain" driving a mobile manipulator
(four-wheel base, extendable column, two arms), built on **ROS 2 Jazzy** and
developed **sim-first** in MuJoCo.

> **Early-stage.** This repo currently holds the architecture, the development
> workflow, and the package skeleton — not yet a working robot. See
> [`docs/design/PROJECT.md`](docs/design/PROJECT.md) for the full design and
> [`docs/design/decisions.md`](docs/design/decisions.md) for the decision log.

## Layout
- `src/robot_*` — ROS 2 packages: `robot_brain`, `robot_skills`, `robot_safety`,
  `robot_backends`, `robot_perception`, `robot_description`, `robot_bringup`.
- `docs/design/` — architecture + decisions (source of truth).
- `docs/features/` — per-feature briefs and reports.
- `.claude/` — coding-agent roles and orchestration (see `DEVELOPMENT.md`).

## Getting started (laptop, headless-friendly)
Requires [pixi](https://pixi.sh). ROS 2 Jazzy is provided via RoboStack.

```bash
pixi install
pixi run build   # colcon build --symlink-install
pixi run test    # colcon test + results
```

## Development
Built by a hierarchy of coding agents (context → implement → red-team → test →
squash-merge). See [`DEVELOPMENT.md`](DEVELOPMENT.md); agent rules are canonical
in [`CLAUDE.md`](CLAUDE.md).

## License
MIT — see [`LICENSE`](LICENSE).
