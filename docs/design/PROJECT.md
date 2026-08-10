# Household Robot — Project Overview

Source of truth for Jaime's project: build an **autonomous household-chore robot**. Seeded 2026-08-09; harness architecture + software stack settled same day (see `decisions.md`).

## Goal
An autonomous household-chore robot driven by an LLM "brain." Target chores (deliberately **not** a single MVP): dishes, organizing/tidying, cleaning up, folding clothes + putting them away, eventually vacuuming. Optimize physical + software design for **LLM operability over raw speed** ("operational first, not too reactive").

## Reality check (grounding)
Even well-funded commercial humanoids (1X NEO, $20k) still rely on human teleoperation for many of these tasks as of early 2026; laundry-folding is an industry-wide unsolved benchmark. Realistic path: **teleoperation-first data collection → learned skills on a narrowing subset**, on a cheap extendable dual-arm wheeled base, with an LLM planner on top. Full autonomy on all target chores is at/beyond the current frontier.

## Physical design (working target)
- **Four-wheel mobile base** (wheels, not legs — cheaper, stable; LG CLOiD validates the wheeled-home form).
- **Extendable vertical lift/column** to reach floor → high shelves. This is the deliberate differentiator vs. the $660 fixed-height DIY crowd. Reuse Stretch / Nori Bot lift design.
- **Tight torso; two extendable arms**, each with an elbow, claw/gripper end-effector. (Field converged on 2 arms; **>2 deferred** as a research risk — coordination + cost + cheap-servo stall-burn.)
- **Head with RGB-D camera + mic** (mic post-MVP). **Standard camera placement** (wrist + front), **not 360** — keeps compatibility with off-the-shelf VLAs. Extra cams for navigation only.

## Harness architecture (settled 2026-08-09)
Hierarchical: **slow LLM brain over fast local skills.** Layers:
1. **Perception:** RGB-D camera → detector + depth → **structured scene JSON with grounded coordinates** (object id, label, 3D pose in base frame, graspable flag) — NOT prose captions. Depth camera required.
2. **Brain** (slow planning cadence; text LLM acceptable given structured perception): scene JSON + semantic map + robot state → emits **skill/pose commands** in a tag/tool-call format, e.g. `<grasp mug_1>`, `<move_gripper right 0.32,0.10,0.85>`, `<navigate_to kitchen_counter>`.
3. **Safety/clamp layer:** reject or clamp illegal/unsafe commands — joint limits, collision checks, gripper **force limits**, velocity caps, e-stop. Present from day one.
4. **Skills** (fast, local): IK/MoveIt for reaching; closed-loop grasp primitive (or learned policy) at contact; Nav2 for driving.
5. **Feedback:** every skill returns status + fresh observation, **event-driven** (not a fire-and-forget timer), fed back to the brain. Closed loop.

- **Control altitude: HYBRID** — skill/pose commands are the default; raw per-DoF joint commands kept only as a debug/teleop escape hatch. The LLM never does inverse kinematics.
- **Localization: semantic map** via SLAM (Nav2). LLM reasons in named places (`kitchen_counter`, `dishwasher`, `laundry_basket`); the nav stack maintains metric pose. No hand-typed coordinates to the LLM.
- **System prompt contents:** skill API (names/args/units/limits) + observation format + current state (joint positions, grippers, battery, room) + safety envelope + 2–3 worked examples. Not raw DoF prose.

## System topology / control plane (settled 2026-08-09)
Three tiers:
- **Pi — OpenClaw chat bot:** user-facing Telegram relay. Owns **user preferences** (natural home: conversational + already has memory); passes relevant prefs into each task. Not a ROS node.
- **Laptop — robot-agent service (OUR harness):** long-lived **FastAPI** service = the perceive-decide-act brain. Holds the ROS 2 connection, the map, and loaded models. **Not** a second OpenClaw bot.
- **Cloud — LLM API:** the brain's reasoning, called from the laptop.

**Flow:** user → Pi bot → `POST /task {goal, chat_id}` over the **WireGuard VPN** → laptop acks a `task_id` → agent runs the loop → pushes progress/"done" back to the Pi bot via callback/webhook → bot relays to user.

**Per-task loop:** coarse **plan** once → execute **one atomic step** (a ROS 2 **action**: goal→feedback→result, cancelable) → **re-perceive** (structured scene JSON + pose from the map) → **replan/adjust** → repeat until done → report completion (+ optional confirming photo, LLM's choice).

**Design rules:**
- **Async, not blocking** (tasks take minutes) — fire-and-callback.
- **Warm persistent service**, not cold spin-up per task (keeps ROS/map/models loaded).
- **Two memories:** world-state (map/objects) = a queried **store/DB**, needed day one; user-preferences = persistent, injected into the system prompt, grows over time.
- **Plan-and-execute with replanning** (not pure greedy, not rigid upfront).
- **Guards (mandatory for a home robot):** max-steps + per-task timeout + stuck-detection; user **stop/cancel** interrupts mid-step; **heartbeat/dead-man** halts if the Pi↔laptop link drops; **one task at a time** (state machine `idle → running(task_id) → done/failed`).
- **Backend-agnostic:** same service drives **MuJoCo now**, real robot later (Mock→Sim→Real). **First milestone:** the whole flow (Telegram → task API → loop → callback → "done") running in **sim, no hardware**.

## Software stack (settled 2026-08-09)
- **Framework:** ROS 2 **Jazzy** (Ubuntu 24.04). [TODO: confirm laptop is 24.04 → Jazzy; if 22.04 → Humble.]
- **Simulation:** **MuJoCo** via `mujoco_ros2_control` (official ros-controls pkg; RGB-D/lidar/IMU/FTS plugins). Best contact physics, headless-friendly. Gazebo as fallback.
- **Kinematics/planning:** **MoveIt 2 + TRAC-IK**. (IKPy/Pinocchio OK for early prototyping.)
- **Navigation:** **Nav2** (SLAM + localization).
- **Visualization:** **Foxglove Studio** (web app, via `foxglove_bridge` on :8765) — headless, replaces rviz.
- **Backend abstraction:** **Mock → Sim (MuJoCo) → Real** behind one `RobotBackend` interface (`execute_skill`, `get_observation`). Develop harness against Mock, then MuJoCo (ground-truth poses first, then the real detector on rendered RGB-D + noise), then hardware.
- **Brain LLM:** hosted API to start (text-only acceptable). Open-source self-host + finetune deferred.
- **Learned skills:** imitation learning (LeRobot, teleop demos) is the first learned-skill path; RL via MuJoCo **MJX** if needed.
- **Headless-first:** entire dev loop is CLI + `launch_testing` + Foxglove; no GUI required.

## Repository & tooling (settled 2026-08-09)
**Structure:** one **base repo** (`household-robot/`, our IP + license) is the center of gravity — it holds the LLM harness, skill API, safety layer, backends, perception glue, robot description, bringup, and tests. Externals are **not** vendored in wholesale. The **skill API is the architectural seam**: the brain (above) is hardware-agnostic IP; drivers/URDF/controllers (below) are swappable. Don't bury glue inside a fork.

Reuse tiers:
- **Depend** (apt/rosdep or conda, no copy): MoveIt 2, Nav2, MuJoCo, `mujoco_ros2_control`, `foxglove_bridge`.
- **Pin** (vcstool `robot.repos` manifest, fixed commit): source repos used unmodified.
- **Fork** (maintained copy, only when patched): e.g. `so101_ros2` or a bringup pkg needing the lift joint — manifest points at our fork.
- **Vendor/crib**: copy a URDF/Xacro into `robot_description` and modify there (don't live-fork a repo just for its model).

```
household-robot/
├── pixi.toml / pixi.lock     # env: ROS 2 + Python + native (see tooling)
├── robot.repos               # vcstool manifest: pinned externals + forks
├── src/
│   ├── robot_brain/          # LLM harness: prompt, tag parser, planner loop
│   ├── robot_skills/         # skill API impl over MoveIt/Nav2
│   ├── robot_safety/         # clamp/limits/force/e-stop
│   ├── robot_backends/       # Mock | Sim(MuJoCo) | Real behind one interface
│   ├── robot_perception/     # detector+depth → scene JSON
│   ├── robot_description/    # URDF/Xacro + MJCF (base + custom column + 2 arms)
│   └── robot_bringup/        # launch (Python), params (YAML), semantic map
└── tests/                    # launch_testing headless integration tests
```

**Languages:** **Python primary (rclpy)** for all of the above (~90%, the IP). **C++ (rclcpp)** only later and only if a custom `ros2_control` controller is unavoidable, or for microcontroller firmware (ESP32/PlatformIO or micro-ROS). Declarative: **XML** (URDF/Xacro, MJCF), **YAML** (params/config), **Python** launch files. **Python 3.12** (Jazzy). The Mock-backend harness is pure Python — runnable on the laptop with no C++ toolchain.

**Package/env manager:** **pixi + RoboStack (`robostack-jazzy`) is primary** — one locked, reproducible, cross-machine env covering ROS 2 + Python + native deps (serves headless + laptop/rented-box parity; MoveIt 2 confirmed on the channel). **Verify** RoboStack coverage of **Nav2, `foxglove_bridge`, `mujoco_ros2_control`**; build any gaps from source inside the pixi env. **uv is the fallback** if we instead use official apt ROS 2 (also aligns with the Pi's apt/Ubuntu deployment) — uv then manages only the PyPI layer, with known ROS 2 venv friction. **colcon still builds** the ROS packages regardless; pixi/uv manage the env, not the build. Don't run both env managers.

**Repo hosting:** scaffold locally now; **pushing public is an outbound action requiring explicit go-ahead** (per AGENTS.md).

## Compute plan
- **Phase 1 (now):** harness in ROS 2 + MuJoCo + MoveIt + Nav2 on the **Xubuntu laptop** (CPU-light) — **$0**.
- **Isaac Sim + rented GPU: DEFERRED (conditional)** — only if a camera-based learned policy hits a photoreal sim-to-real wall, or to use NVIDIA GR00T/cuRobo. Not a planned phase. If triggered: rent RTX 4090 hourly (~$0.15–0.70/hr, RunPod/Vast), headless.
- **Raspberry Pi 4: deployment target only** (onboard sensor/motor loop + LLM calls), not a dev/sim box. May be swapped for a Jetson if onboard inference is wanted.
- **Buy vs rent:** rent first; buy a used RTX 3090/4090 only if GPU spend exceeds ~$150–200/mo sustained.

## Reuse map / fork strategy (settled 2026-08-09)
Estimate: **~85% of the software stack already exists** and should be reused/forked; the novel **~15%** is the LLM harness itself. Reusing the right things does **not** force different hardware — it matches the planned dual-SO-101 + wheeled base + RGB-D.

Strategy: **don't fork one monolith.** Consume frameworks as dependencies, fork *one* ROS 2 robot reference for description+bringup, build the brain.

| Layer | Exists? | Action |
|---|---|---|
| Frameworks: MoveIt 2, Nav2, MuJoCo, `mujoco_ros2_control`, LeRobot, Foxglove | 100% | **Dependencies** — configure, never fork. Hardware-agnostic (URDF-driven). |
| Arm-in-ROS 2: SO-101 drivers, URDF, teleop, dataset capture | Yes | Reuse/fork **`so101_ros2`** (msf4-0) and the **SO101 ROS 2 Workspace** (so101-ros2.readthedocs.io) — bridge LeRobot ↔ ROS 2. |
| Full mobile-manipulator ref (hardware + ROS bringup) | Yes | **Fork ONE.** Evaluate **AhaRobot** first (aha-robot.github.io — $1k dual-arm, open ROS + firmware + CAD + dataset + arXiv), then the "ROS 2 + dual SO-101 + browser HMI" bimanual project, then **youfork** (github.com/youtalk/youfork — clean ROS 2 bringup pattern). |
| Mechanical BOM/STL | Yes | Crib XLeRobot (Vector-Wangel/XLeRobot), AhaRobot, Nori (arXiv 2605.16537). |
| Perception (grounded scene JSON) | Models yes | Reuse Grounding DINO + SAM + depth; **build** the scene-JSON glue. |
| LLM harness / brain (tag commands, skill API, feedback loop, semantic map) | Barely | **Build.** Crib **Stretch AI** (Hello Robot) architecture — closest open analogue. This is the project's IP. |
| Skill/VLA models to crib later | Yes | Pi0/Pi0.5, GR00T N1.7, Gemini Robotics-ER, OpenVLA, Octo. |

**Do NOT fork XLeRobot wholesale:** it's LeRobot/Python (not ROS 2 — framework mismatch), **fixed-height** (IKEA cart base, no lift — contradicts our extendable-column differentiator), and low-spec (~40 cm reach, <1 kg/arm). Use it as a BOM/mechanics crib only.

**Hardware coupling note:** forking a robot's *software* drags in its *hardware assumptions* only at the description/driver layer (swappable later, since the brain talks to a skill API not hardware). AhaRobot's hardware ≈ our plan; the **extendable column is custom regardless** (no reference has it) and is our differentiator.

**Payload caveat (for the hardware open-question):** SO-101's ~40 cm reach + <1 kg/arm won't handle a loaded plate or laundry pile. Fine for harness/sim; flag "may need beefier arms/servos" for real chores.

## Open questions (still genuinely open)
- Lift mechanism + reach envelope; BOM for the extendable column.
- Brain LLM choice (which hosted model) + when/if to move to self-hosted finetune.
- Which chore to build the first end-to-end learned skill for (data-collection target).
- Gripper design (claw type, force sensing) for fragile-object handling.
- Depth-camera model (RealSense-class) selection.

## Next steps
1. Confirm laptop Ubuntu version → pin ROS 2 distro.
2. Stand up ROS 2 + MuJoCo + MoveIt + Foxglove on the laptop; get the **Mock-backend harness loop** running (LLM → tag parse → mock skill → observation).
3. Define the `RobotBackend` interface + initial skill API (`navigate_to`, `move_gripper`, `grasp`, `place`, `extend_column`, `open/close_gripper`).
4. Build a URDF/MJCF of the robot (4-wheel base + column + 2 arms) for MuJoCo.
5. GitHub repo skeleton (structure, license, CI) — pending explicit go-ahead.
