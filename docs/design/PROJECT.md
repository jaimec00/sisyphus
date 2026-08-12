# Household Robot — Project Overview

Source of truth for Jaime's project: build an **autonomous household-chore robot**. Seeded 2026-08-09; harness architecture + software stack settled same day (see `decisions.md`). **Brain-location pivoted 2026-08-11 — the brain IS the OpenClaw Telegram agent itself, not a custom harness (see D21); scope is a slow low-cost chore robot, not a frontier competitor (D22).**

## Goal
An autonomous household-chore robot driven by an LLM "brain." Target chores (deliberately **not** a single MVP): dishes, organizing/tidying, cleaning up, folding clothes + putting them away, eventually vacuuming. Optimize physical + software design for **LLM operability over raw speed** ("operational first, not too reactive"). **Scope (D22): a slow, low-cost robot that eventually gets some chores done — explicitly not competing with the frontier; classical/scripted skills first, learned skills only where unavoidable.**

## Reality check (grounding)
Even well-funded commercial humanoids (1X NEO, $20k) still rely on human teleoperation for many of these tasks as of early 2026; laundry-folding is an industry-wide unsolved benchmark. Realistic path: **teleoperation-first data collection → learned skills on a narrowing subset**, on a cheap extendable dual-arm wheeled base, with an LLM planner on top. Full autonomy on all target chores is at/beyond the current frontier — and we are **not** chasing that frontier (D22): the near-term win is the LLM-planner + skill-API + sim loop working end-to-end, plus classical pick-and-place of rigid objects on cheap hardware, with teleop filling the gaps.

## Physical design (working target)
- **Four-wheel mobile base** (wheels, not legs — cheaper, stable; LG CLOiD validates the wheeled-home form).
- **Extendable vertical lift/column** to reach floor → high shelves. This is the deliberate differentiator vs. the $660 fixed-height DIY crowd. Reuse Stretch / Nori Bot lift design.
- **Tight torso; two extendable arms**, each with an elbow, claw/gripper end-effector. (Field converged on 2 arms; **>2 deferred** as a research risk — coordination + cost + cheap-servo stall-burn.)
- **Head with RGB-D camera + mic** (mic post-MVP). **Standard camera placement** (wrist + front), **not 360** — keeps compatibility with off-the-shelf VLAs. Extra cams for navigation only.

## Harness architecture (settled 2026-08-09; brain implementation pivoted 2026-08-11 — D21)
Hierarchical: **slow LLM brain over fast local skills.** Layers:

> **Brain implementation (D21):** the brain is the **OpenClaw Telegram agent**, which drives the skills below as **MCP tools**. The layered design here is unchanged — only *who runs the brain* changed (OpenClaw's native tool-call loop replaces a custom harness; no tag-parser).
1. **Perception:** RGB-D camera → detector + depth → **structured scene JSON with grounded coordinates** (object id, label, 3D pose in base frame, graspable flag) — NOT prose captions. Depth camera required.
2. **Brain** (the OpenClaw agent; slow planning cadence; text LLM fine given structured perception): scene JSON + semantic map + robot state → issues **skill/pose commands as native MCP tool-calls**, e.g. `grasp(mug_1)`, `move_gripper(right, 0.32,0.10,0.85)`, `navigate_to(kitchen_counter)`. (The earlier `<grasp mug_1>` tag format is obsoleted by native tool-calling — D21.)
3. **Safety/clamp layer:** reject or clamp illegal/unsafe commands — joint limits, collision checks, gripper **force limits**, velocity caps, e-stop. Present from day one.
4. **Skills** (fast, local): IK/MoveIt for reaching; closed-loop grasp primitive (or learned policy) at contact; Nav2 for driving.
5. **Feedback:** every skill returns status + fresh observation, **event-driven** (not a fire-and-forget timer), fed back to the brain. Closed loop.

- **Control altitude: HYBRID** — skill/pose commands are the default; raw per-DoF joint commands kept only as a debug/teleop escape hatch. The LLM never does inverse kinematics.
- **Localization: semantic map** via SLAM (Nav2). LLM reasons in named places (`kitchen_counter`, `dishwasher`, `laundry_basket`); the nav stack maintains metric pose. No hand-typed coordinates to the LLM.
- **System prompt contents:** skill API (names/args/units/limits) + observation format + current state (joint positions, grippers, battery, room) + safety envelope + 2–3 worked examples. Not raw DoF prose.

## System topology / control plane (settled 2026-08-09; brain-location pivoted 2026-08-11 — D21)
Three tiers (**D21** — the brain is the OpenClaw agent, not a custom service):
- **Pi — OpenClaw brain-bot:** the user-facing Telegram agent that **is the robot's brain**. Owns **user preferences** (native OpenClaw memory), plans, and drives the robot by calling its skills as **MCP tools**, reading structured results back. A conversational agent, not a ROS node.
- **Laptop — robot-side service:** hosts the **`robot_mcp` tool server** + skill impls + the safety/clamp layer + perception + the ROS 2 connection, the map, and loaded models/sim. The body + reflexes + senses, exposed through the one **skill-API seam**. **Not** the brain; not a second OpenClaw bot.
- **Cloud — LLM API:** the brain's reasoning, called by the OpenClaw agent.

**Flow:** user → OpenClaw brain-bot (Telegram) → **MCP tool-call** to the robot-side service (over the **WireGuard VPN** / local socket) → skill executes, **safety-clamped** → structured `SkillResult` + fresh `Observation` returned → the agent's tool-loop replans and calls the next skill → until done → replies to the user (+ optional confirming photo).

**Per-task loop:** the OpenClaw agent's **native turn loop IS the loop** — coarse **plan** → call **one skill** (MCP tool) → read the returned observation → **replan/adjust** → repeat until done. No custom orchestration to build.

**Design rules:**
- **Warm robot-side service** (keeps ROS/map/models/sim loaded); the OpenClaw agent handles the conversational, event-driven front.
- **Two memories:** world-state (map/objects) = a queried **store/DB**, needed day one (owned by the robot-side service — landed as `robot_world`, D23); user-preferences = **OpenClaw's native memory**, grows over time.
- **Plan-and-execute with replanning** (not pure greedy, not rigid upfront) — emergent from the agent's tool-loop.
- **Guards (mandatory for a home robot) live server-side, below the tool boundary — never trusted to the LLM:** max-steps + timeout + stuck-detection; user **stop/cancel** interrupts; **heartbeat/dead-man** halts if the Pi↔laptop link drops; **e-stop**; **one task at a time**. (If OpenClaw can't drive these from the agent side, that is exactly a D21 trigger to add the custom service — but the *enforcement* belongs in the robot-side service regardless.)
- **Backend-agnostic:** same skills drive **Mock now**, MuJoCo next, real robot later (Mock→Sim→Real). **First milestone:** the whole flow (Telegram → OpenClaw agent → MCP skill calls → **Mock** backend → reply) running with **no hardware and no custom harness**.

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
Estimate: **~85% of the software stack already exists** and should be reused/forked; the novel **~15%** is the LLM brain + glue — and under **D21** the brain itself is largely **OpenClaw**, shrinking our build further to the skill API + MCP surface, safety, perception glue, robot description, and prompt design. Reusing the right things does **not** force different hardware — it matches the planned dual-SO-101 + wheeled base + RGB-D.

Strategy: **don't fork one monolith.** Consume frameworks as dependencies, fork *one* ROS 2 robot reference for description+bringup, and **expose the skills to the OpenClaw brain via `robot_mcp`** (D21) — build a custom brain only if OpenClaw proves lacking.

| Layer | Exists? | Action |
|---|---|---|
| Frameworks: MoveIt 2, Nav2, MuJoCo, `mujoco_ros2_control`, LeRobot, Foxglove | 100% | **Dependencies** — configure, never fork. Hardware-agnostic (URDF-driven). |
| Arm-in-ROS 2: SO-101 drivers, URDF, teleop, dataset capture | Yes | Reuse/fork **`so101_ros2`** (msf4-0) and the **SO101 ROS 2 Workspace** (so101-ros2.readthedocs.io) — bridge LeRobot ↔ ROS 2. |
| Full mobile-manipulator ref (hardware + ROS bringup) | Yes | **Fork ONE.** Evaluate **AhaRobot** first (aha-robot.github.io — $1k dual-arm, open ROS + firmware + CAD + dataset + arXiv), then the "ROS 2 + dual SO-101 + browser HMI" bimanual project, then **youfork** (github.com/youtalk/youfork — clean ROS 2 bringup pattern). |
| Mechanical BOM/STL | Yes | Crib XLeRobot (Vector-Wangel/XLeRobot), AhaRobot, Nori (arXiv 2605.16537). |
| Perception (grounded scene JSON) | Models yes | Reuse Grounding DINO + SAM + depth; **build** the scene-JSON glue. |
| LLM brain (planner, feedback loop) | Use OpenClaw | **Use the OpenClaw agent as the brain (D21)** — native tool-calling + memory. Stretch AI = architecture reference only. Build a custom harness only if OpenClaw proves lacking. |
| Skill API + MCP surface, safety layer, perception→scene-JSON glue, robot description, prompt design | Build | **Build — this is the remaining IP** (D21). The seam the OpenClaw brain drives, plus the body/reflexes/senses below it. |
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
1. **First milestone (D21):** stand up a dedicated **OpenClaw `robot` agent** whose system prompt carries the skill API + observation format + safety envelope + worked examples; wire it to **`robot_mcp`** over the **Mock** backend; prove end-to-end — text "clear the table" → tool-calls `navigate_to`/`grasp`/`place` in a loop against Mock, safety-clamped, replies. No FastAPI, no tag-parser.
2. Expand `robot_mcp` to expose the full skill set (`navigate_to`, `move_gripper`, `grasp`, `place`, `extend_column`, `open/close_gripper`, `get_observation`) over the `RobotBackend` seam; land the **safety/clamp layer** server-side (D17).
3. ~~Add the **world-state store** (map/objects) queried by the robot-side service.~~ **Done (D23):** `robot_world` holds the map + object registry as a JSON-file store (read-only seed + atomically-written live state); the Mock backend reads and mutates it, and `robot_mcp --world-state PATH` makes the world survive a restart. Still to come: wrapping it in a ROS 2 query service, and letting perception write into it.
4. Confirm laptop Ubuntu version → pin ROS 2 distro; then swap Mock → **MuJoCo** behind the same skills.
5. Build a URDF/MJCF of the robot (4-wheel base + custom column + 2 arms) for MuJoCo.
6. Classical skills first (D22): MoveIt pick-and-place of rigid objects + Nav2; learned skills only where unavoidable.
