# Feature brief: mock-skill-api

> Written by Sisyphus (top manager). This is the fixed target every downstream
> agent measures against.

## Goal
Establish the foundational contract of the robot's control interface and its
first backend, in **pure Python** (no ROS runtime, no physics). Define the
**skill API** — the typed set of high-level skills the brain will call — and
implement the **Mock backend**: an in-memory world model that accepts skill
calls and returns closed-loop structured observations. This is the seam every
later layer binds to (Sim/Real backends, safety layer, brain, ROS 2 action
transport), and it lets us run the whole perceive → act → observe loop
end-to-end with zero hardware.

## Owned paths
- `src/robot_skills/` — the skill API contract (skill + observation + result types).
- `src/robot_backends/` — the `RobotBackend` interface + `MockBackend` implementation.
> Do not modify other packages. No ROS 2 action/message wiring in this feature.

## Design constraints (from docs/design/PROJECT.md; decisions D2/D3/D9/D16)
- **Backend abstraction:** a single `RobotBackend` interface; `MockBackend` is
  the first impl; `SimBackend`/`RealBackend` come later and must satisfy the
  same interface.
- **Skill-level altitude:** skills are *goals* (navigate_to, grasp, move_gripper,
  place, extend_column, open/close_gripper) — never raw joints.
- **Structured perception:** observations are structured data with coordinates
  (objects with id/label/pose/graspable + robot pose/gripper state), serializable
  to a JSON-able dict — never prose.
- **Closed loop:** every skill returns a status + a fresh observation.
- **Pure Python, deterministic:** no ROS graph required to import/run; no
  wall-clock/random nondeterminism in the Mock (fixed/seedable) so tests are
  reproducible.

## Acceptance criteria
- [ ] `robot_skills` defines typed skills: `navigate_to(place)`,
  `move_gripper(side, pose)`, `grasp(object_id)`, `place(pose)`,
  `extend_column(height)`, `open_gripper(side)`, `close_gripper(side)` — a clean,
  documented Python API (dataclasses/enums/Protocol; final shape at the
  implementer's discretion).
- [ ] `robot_skills` defines `Observation` (robot pose incl. current place +
  column height + per-gripper state/held object; list of objects with
  id/label/3D pose/graspable) and `SkillResult` (status ∈ {ok, failed}, reason,
  resulting observation). All serializable to a plain dict / JSON.
- [ ] `robot_backends` defines a `RobotBackend` interface (`reset()`,
  `get_observation()`, `execute(skill) -> SkillResult`).
- [ ] `MockBackend` implements it with a small deterministic world model that
  mutates plausibly: navigate changes place; grasp attaches a present, graspable
  object to a free gripper; place / open drops the held object at/near the robot;
  extend_column sets height; open/close toggles the gripper.
- [ ] Failure paths return `status=failed` with a clear reason (grasp
  missing/ungraspable object; grasp with an occupied gripper; place/close-drop
  with an empty gripper; navigate to an unknown place) and do not corrupt state.
- [ ] Importable and runnable with **no ROS 2 graph running**.

## Required tests
- **Per-skill round trip:** each skill call returns the expected status and the
  mutation is reflected in the next `get_observation()`.
- **Failure paths:** grasp-missing, grasp-occupied, place-empty, navigate-unknown
  each return `failed` with a reason and leave world state intact.
- **Serialization:** `Observation` and `SkillResult` round-trip to/from a plain
  dict (JSON-safe).
- **Scenario/composition:** `navigate_to(kitchen) → grasp(mug_1) →
  navigate_to(table) → place(...)` leaves the world in the expected state and
  every step returns `ok` — proving closed-loop composition.

## Out of scope / non-goals
Real physics / MuJoCo (`SimBackend`), ROS 2 action or message transport, the
safety/clamp layer, perception models, the LLM brain, IK/MoveIt. Each is a later
feature. The API shape should *allow* a safety wrapper and a ROS action layer
without redesign — but do not build them here.

## References
- `docs/design/PROJECT.md` (Harness architecture; Software stack; System topology).
- `docs/design/decisions.md` — D2 (control altitude), D3 (perception),
  D9 (backend abstraction), D16 (control plane).
- `CLAUDE.md` architectural invariants.

## Run (V1, manual)
On the laptop as `sisyphus-dev`, in the repo: `/run-feature mock-skill-api`.
