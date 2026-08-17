# Sisyphus — Project Overview

Build an **autonomous household-chore robot**: an LLM "brain" driving a cheap,
extendable dual-arm mobile manipulator, on ROS 2, developed sim-first. Target
chores (deliberately **not** a single MVP): dishes, organizing/tidying, cleaning
up, folding clothes + putting them away, eventually vacuuming. The physical and
software design optimizes for **LLM operability over raw speed** ("operational
first, not too reactive"). Seeded 2026-08-09.

**One fact, one home.** This file owns the *goal*, the *grounding*, the *open
questions*, and the *next steps* — nothing else. Every hardware, architecture,
stack, or packaging fact lives in exactly one of the docs below; this file
**points**, it does not restate.

## Where things live
| You want… | Read |
|---|---|
| **What the robot currently is** — body, brain, seams, stack, packaging | [`spec.md`](spec.md) |
| **Why** any of it is that way — the append-only decision log (D1–D28) | [`decisions.md`](decisions.md) |
| **How the body gets built** — URDF/MJCF roadmap + PR sequence | [`urdf-mjcf-pr-breakdown.md`](urdf-mjcf-pr-breakdown.md) |
| How we work — agents, loop, worktrees, merges | [`../../AGENTS.md`](../../AGENTS.md) |

`decisions.md` is the source of truth: **where any doc disagrees with it,
`decisions.md` wins.** `spec.md` is the flattened HEAD of those decisions, and
absorbs the reuse/fork strategy (D12) and repo-structure (D13) detail this file
used to carry.

## Reality check (grounding)
Even well-funded commercial humanoids (1X NEO, $20k) still rely on human
teleoperation for many of these tasks as of early 2026; laundry-folding is an
industry-wide unsolved benchmark. Realistic path: **teleoperation-first data
collection → learned skills on a narrowing subset**, on a cheap extendable
dual-arm wheeled base, with an LLM planner on top. Full autonomy on all target
chores is at or beyond the current frontier — and we are **not** chasing that
frontier (D22): the near-term win is the LLM-planner + skill-API + sim loop
working end-to-end, plus classical pick-and-place of rigid objects on cheap
hardware, with teleop filling the gaps.

Estimate that **~85% of the software stack already exists** and should be
reused or cribbed; the novel remainder is the skill API + MCP surface, the
safety layer, perception → scene JSON, the robot description, and the prompt
design that makes an LLM drive it well (D12, D21). The genuinely hard, genuinely
custom parts — the extendable column and robust fragile-object grasping — are
both the differentiators and the real risks (D22).

## Status
- **Landed:** the skill/observation seam, the safety layer, the Mock backend,
  `robot_mcp` over that seam, the `robot_world` JSON store (D23), the
  self-discovering launch path + boot-smoke gate (D24), and `robot_description`
  as a real package with a CI expand/parse gate (D27, PR #62).
- **In flight:** the URDF/MJCF body — PR1 done, PR2 (base) next. Sequence and
  per-PR acceptance in [`urdf-mjcf-pr-breakdown.md`](urdf-mjcf-pr-breakdown.md).
- **Not started:** MuJoCo backend behind the same skills; perception writing
  into `robot_world`; any real hardware (nothing purchased).

## Open questions (still genuinely open)
- **Brain LLM choice** (which hosted model) + when/if to move to a self-hosted
  finetune.
- **Which chore** to build the first end-to-end learned skill for — i.e. the
  data-collection target.
- **Fragile-object grip:** whether the stock parallel-jaw + a compliant fin-ray
  fingertip swap (the reserved first upgrade, D26) is enough, or whether force
  sensing is needed.
- **Payload ceiling:** SO-101's ~0.4 m reach and ~0.25–0.5 kg/arm won't handle a
  loaded plate or a laundry pile. Fine for harness/sim; the real-hardware answer
  is the penciled PiPER upgrade (D26), which costs the single bus — unresolved.
- **Nori Bot is UNVERIFIED** (arXiv 2605.16537) — the column crib and the
  agent↔hardware seam both lean on a paper we have not read (D26).
- **RoboStack coverage** of Nav2 / `foxglove_bridge` / `mujoco_ros2_control` —
  verify, and source-build any gaps inside the pixi env (D15).

*(Resolved and moved to [`spec.md`](spec.md): lift mechanism, gripper type,
depth-camera class, base geometry — all settled by D26.)*

## Next steps
1. **Build the body:** work the URDF/MJCF PR sequence — PR2 (3-omniwheel base)
   is next off the harness PR1 landed. See
   [`urdf-mjcf-pr-breakdown.md`](urdf-mjcf-pr-breakdown.md).
2. **Prove the brain end-to-end:** an OpenClaw `robot` agent whose system prompt
   carries the skill API + observation format + safety envelope + worked
   examples, wired to `robot_mcp` over the **Mock** backend — text "clear the
   table" → tool-calls in a loop, safety-clamped, replies (D21).
3. **Swap Mock → MuJoCo** behind the same skills, once the body exists (D9).
4. **Wrap `robot_world` in a ROS 2 query service** and let perception write into
   it (D23).
5. **Classical skills first** (D22): MoveIt pick-and-place of rigid objects +
   Nav2; learned skills only where unavoidable.
