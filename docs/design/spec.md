# Sisyphus — Current Spec (the resolved *what*)

**This is the flattened HEAD of every decision: what the robot *currently is*.**
It owns no rationale — every fact cites the decision(s) that govern it, and the
*why* lives in [`decisions.md`](decisions.md), which is the canonical source of
truth. **Where any doc disagrees with `decisions.md`, `decisions.md` wins**; where
`decisions.md` has moved on, this file is re-flattened rather than patched.

*One fact, one home.* A doc either **owns** a fact or **points** to it, never
both. This file owns current state; `decisions.md` owns why we got here;
[`PROJECT.md`](PROJECT.md) owns the goal and the open questions;
[`urdf-mjcf-pr-breakdown.md`](urdf-mjcf-pr-breakdown.md) owns the URDF/MJCF
roadmap.

Flattened through **D28** (2026-08-13).

---

## Governing hardware decision
**Every actuated joint lives on one Feetech STS3215 / LeRobot bus** (D26). That
single-servo-ecosystem constraint — not any individual component pick — is what
governs the base, column, arm, and gripper choices below: one URDF actuator
model, one MJCF, one bus, and a forked simulator rather than an authored one.

## Body

| Subassembly | Current state | Governing D |
|---|---|---|
| **Base** | **LeKiwi 3-omniwheel holonomic** base | D26 (supersedes D1's 4-wheel) |
| **Column** | **Linear-rail STS3215 lift on the arm bus**, Nori-style; **one prismatic joint** (`column_lift`), travel **0.00–1.20 m** | D26 (supersedes D1's belt drive and the "custom, no reference" framing); limits per D23's `RobotModel` |
| **Arms** | **2× SO-101** (Feetech STS3215, 6-DOF); authored as a **swappable xacro macro** (`prefix`/`side` + mount transform) | D26 |
| **Gripper** | **Stock SO-101 parallel-jaw** (STS3215, the arm's 6th DOF); **fingertip is a swappable link** so fin-ray/compliant fingers are a geometry swap, not a re-model | D26; aperture/`grasped` semantics per D19 |
| **Head camera** | **One head-mounted RGB-D.** URDF reserves a `head_camera_link` + REP-103 optical frame on `column_top`; **buy nothing yet** — Mock and MuJoCo run on ground-truth poses | D26, D21, D3 |

**Penciled real-hardware parts (not committed, nothing purchased):** **PiPER**
(Agilex, CAN bus) as the arm payload upgrade — note it *breaks* the single bus,
so it is a real-hardware-phase decision, not a sim one; **Orbbec Gemini 335** as
the head RGB-D (RealSense D435i compatibility fallback, OAK-D Lite budget
floor) (D26).

**Explicitly deferred:** suction end-effector (not a Feetech servo → breaks the
single bus; doesn't map onto the aperture/`grasped` seam — a future *second*
end-effector decision); wrist cameras; microphone (D26).

## Substrate we crib from
- **XLeRobot** — the geometric substrate for **base + arms** (LeKiwi base + 2×
  SO-101; URDFs exist). This promotes it from D12's "BOM/mechanics crib only"
  (D26).
- **Nori Bot** (arXiv 2605.16537) — crib for the **linear-rail STS3215 column**
  and the agent↔hardware seam. **UNVERIFIED until the paper is read** (D26).
- **`so101_ros2`** — the arm-in-ROS 2 layer (D12, D26).

## Brain & architecture
- **The brain IS a dedicated OpenClaw Telegram agent on the Pi**, driving the
  robot's skills as **`robot_mcp` MCP tools**. OpenClaw's native tool-call loop
  *is* the perceive → decide → act → re-perceive loop — there is **no custom
  planner loop and no tag/tool-call parser** (D21, supersedes the brain-location
  half of D16). User preferences live in OpenClaw's native memory.
- **A custom `robot_brain` planner loop and the FastAPI task-service are
  deferred to contingency triggers** — built only if OpenClaw proves lacking
  (context bloat, headless autonomy without a chat round-trip, or a control
  cadence tighter than chat-paced) (D21).
- **The laptop is the robot-side service:** `robot_mcp` tool server + skill
  impls + safety/clamp layer + perception + the ROS 2 connection, map, models,
  and sim — all behind the one **skill-API seam** (D21, D13).
- **Safety, e-stop, and hard guards are enforced server-side, below the tool
  boundary — never by the LLM** (D4, D17, D21). Split by *kind* of limit:
  **kinematic/workspace reachability** is the **backend's** refusal up front;
  **dynamic safety** (clamps, collision-during-motion, force limits, e-stop) is
  the **safety layer's** in-flight clamp/abort (D17).
- **Determinism boundary:** everything *below* the tool call is deterministic
  Python + physics. The **only** stochastic element is the brain's choice of
  tool + args; the **sole additional neural model** is perception, and only on
  the real/detector path (Mock and MuJoCo ground-truth modes are model-free).
  **No second LLM** (D21).
- **Scope:** a slow, low-cost chore robot, explicitly **not** competing with the
  frontier — classical/scripted skills (MoveIt pick-and-place of rigid objects +
  Nav2 + the LLM planner) first, learned skills only where classical methods
  can't reach (D22).

## Seams & data
- **Backend abstraction:** `Mock → Sim (MuJoCo) → Real` behind one
  `RobotBackend` interface; new code works against Mock first (D9).
- **Wire format:** one version-stamped schema (`Observation` / `SkillResult` /
  skill signatures) with `SCHEMA_VERSION`; additive optional field = non-breaking,
  remove/rename/retype = version bump + all binders updated **atomically in the
  same PR**, guarded by a golden-fixture test (D18).
- **Grasp semantics:** `close_gripper` on nothing is a **successful** skill
  reporting `grasped=false`; errors stay reserved for "couldn't run"; over-force
  is a safety event, not an error (D19).
- **World state:** `robot_world` holds the map + object registry as a
  JSON-file store — read-only shipped **seed** + atomically-written **live**
  file, one document schema, its own `world_schema_version`, persistence
  **opt-in**. The store holds the scene; the backend holds the robot; the world
  file never describes the robot's body (D23).
- **Perception** emits structured scene JSON with grounded 3D coordinates, not
  prose (D3).
- **Control altitude is hybrid:** skill/pose commands are the default; raw
  per-DoF joints are a debug/teleop escape hatch only — the LLM never does IK
  (D2).

## Description & packaging
- **`robot_description` is `ament_python`** — not `ament_cmake`, deliberately
  (D27). `data_files` installs `share/robot_description/{urdf,meshes}`.
- **One top-level `urdf/robot.urdf.xacro`** over per-subassembly includes
  (`base.xacro`, `column.xacro`, `arm.xacro`); `base_link` is declared at the
  top level; includes are **relative** so a source checkout and the installed
  tree expand identically; install uses `glob()`, never a hand-maintained list
  (D27, applying D24).
- **The base is built** (D29): `base.xacro` is a parametric 3-omniwheel
  holonomic base — `base_chassis_link` and `base_footprint` as fixed children
  of `base_link`, plus one wheel macro instantiated at **60°/180°/300°** on
  `continuous` joints named `base_{left,back,right}_wheel` (the LeRobot
  driver's motor keys). Each wheel's spin axis is the **outward radial**, and
  the driver's `[240, 0, 120]` array is *rolling directions*, not mount
  positions. Every dimension is an `<xacro:property>`; `wheel_radius = 0.05`
  and `base_radius = 0.125` are sourced from LeRobot's `lekiwi.py`, the rest
  are marked estimates. Because `base_footprint` is a child of `base_link`,
  odometry must publish `odom → base_link`, never `odom → base_footprint`.
- **Geometry is primitives; `meshes/` is still empty** (D29). No third-party
  mesh is vendored, so D27's flat-glob limit stands and the `os.walk` install
  rewrite is owed by the first PR that lands real meshes (expected: the arms).
  Column, arms, grippers, camera and the MJCF are still to come.
- **The CI gate expands the *installed* copy**, resolved through the ament index
  with no source-tree fallback: `xacro` CLI expands → `check_urdf` parses →
  `urdf_parser_py` re-parses and the link set is asserted **exactly** → every
  file the description names (`<mesh>`, `<texture>`) resolves in the installed
  share tree (D27); plus, from the base on, that there are exactly three
  `continuous` wheel joints with the expected names, that the driver's
  body→wheel matrix rebuilt from the model equals LeRobot's own constant (the
  absolute check — the relational ones survive a left/right wheel swap), that
  they are mounted 120° apart on one circle with radial spin axes, that
  `base_footprint` sits one wheel radius below the axle plane, that every body
  link has visual + collision geometry and the chassis clears the wheels, that
  every non-frame link has a real inertial, and that
  **`robot_state_publisher` loads the model** — it builds a KDL tree, so it
  rejects models `check_urdf` accepts (D29).
- **`RobotModel` is still in code** and will later be read *from* the URDF —
  the URDF is canonical for kinematics/geometry, MJCF carries a thin sim-only
  physics layer on top (D23; roadmap in
  [`urdf-mjcf-pr-breakdown.md`](urdf-mjcf-pr-breakdown.md)).

## Stack & environment
- **ROS 2 Jazzy**, Python 3.12; **Python (rclpy) primary**, C++ only for an
  unavoidable custom `ros2_control` controller or MCU firmware (D5, D14).
- **MuJoCo** via `mujoco_ros2_control` (Gazebo fallback); **MoveIt 2 +
  TRAC-IK**; **Nav2**; **Foxglove Studio** via `foxglove_bridge` over rviz
  (D6, D7, D5, D8).
- **pixi + RoboStack (`robostack-jazzy`)** is the env manager, `colcon` still
  builds; uv is the fallback only if we move to apt ROS 2 (D15).
- **Compute:** develop on the Xubuntu laptop (CPU, $0); the Pi 4 is a
  deployment target only; **Isaac Sim + rented GPU is deferred/conditional**
  (D11, D10).
- **Repo:** one base repo holds all glue/IP; externals come in via
  depend / pin (vcstool) / fork-only-when-patched / vendor-crib tiers, never
  buried in a fork (D13).

## Ops & gates
- **Launch path discovers itself:** `scripts/robot-mcp-launch.sh` builds
  `PYTHONPATH` from `src/*/package.xml` — hand-maintained package lists on the
  deploy path are banned, and the gate boots the server through the same
  launcher the deployment uses (D24).
- **The red-team is read-only *to the worktree*, not shell-less:** it runs the
  code and labels every finding VERIFIED or UNVERIFIED, never edits source or
  tests, and leaves the tree exactly as found (D25).
- **The test-count floor maintains itself:** `scripts/test_baseline.json` is
  written by `pixi run test` — it ratchets **up** automatically on an otherwise-
  green run, comes **down** only under `ALLOW_TEST_DECREASE=1`, counts tests that
  **ran** (a skip is a deletion), and is never written from a non-green run
  (D28).
- **The operating prompt's body claims are gated inside `robot_brain`, not from
  the URDF:** a body claim whose owner lives on the brain's side of the skill
  API is read from that owner (arm count from `Side`, column travel and arm
  reach from `RobotModel`); the drivetrain has no such owner, so
  `SUPERSEDED_BODY_CLAIMS` in `test/test_prompt_drift.py` pins it and **must
  gain a row whenever a decision supersedes a body fact that has no live
  owner** — nothing detects the need. Find the owner first; a ledger row is the
  fallback, not the rule. The same applies to a body fact the prompt gains for
  the first time — the head camera, which the prompt does not mention today — as
  it lands **ungated** unless its author reads it from an owner or pins it. `robot_brain` takes **no
  dependency on `robot_description`** (D30).
- **Sisyphus is the sole merger;** managers and operational agents stop at an
  open, green PR (D20).
