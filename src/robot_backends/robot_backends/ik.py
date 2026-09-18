# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""A small, pure damped-least-squares IK solver for one 5-DOF arm.

The SO-101 arm has five revolute joints but the gripper task frame lives in
six dimensions (three translation + three rotation), so an arbitrary pose is
generally unreachable; the solver's job is to decide whether a *given* pose is
on the reachable manifold and, if so, to return joint values that put the
gripper there.  A residual that does not fall below tolerance is therefore an
honest "out of reach" signal, not a solver failure (see ``status.md`` R1: an
analytic closed form does not exist for this chain, and a sphere test is a
worse proxy than convergence itself).

The algorithm is damped least squares (Levenberg-Marquardt) over a
**forward-difference numeric Jacobian** of the six-vector task error, with
multi-start to escape the joint-limit stalls a single descent falls into::

    e(q) = [ p_target - p_current ; log(R_target @ R_current.T) ]
    J[:, j] = (e(q + eps*e_j) - e(q)) / eps
    dq = -J.T @ solve(J @ J.T + lam**2 * I6, e)

The solver is deliberately ROS-free, numpy-only, and **side-effect-free**: it
snapshots the arm's qpos slots on entry and restores them (with one
``mj_forward``) before returning, so a refused solve leaves ``mjData`` exactly
as it found it.  That is what lets the backend refuse a skill up front without
half-moving the world (``_SkillRefused`` discipline).

All orientations are 3x3 rotation matrices, never hand-rolled quaternion
products -- the skill API's ``Quaternion`` is (x, y, z, w) and MuJoCo's
``xquat`` is (w, x, y, z), and mixing the two conventions silently produces a
wrong Jacobian (status.md R1).
"""

from __future__ import annotations

from typing import Callable, Sequence

import numpy as np

__all__ = ['solve_ik']

#: Forward-difference step for the numeric Jacobian (metres/radians).
_EPS = 1e-6

#: Initial damping.  Divided by two on an accepted step, doubled on a reject.
_LAMBDA0 = 1e-3

#: Damping floor, so an accepted run never loses its conditioning entirely.
_LAMBDA_MIN = 1e-7

#: Iteration budget per start.
_MAX_ITERS = 400

#: Convergence tolerance: translation (m) and rotation (rad) held separately.
_TOL = 1e-4

#: Extra random starts after the q=0 start; deterministic via the seed below.
_RANDOM_STARTS = 8

#: Seed for the multi-start RNG -- fixed so a solve is reproducible.
_SEED = 0


def _vee(skew: np.ndarray) -> np.ndarray:
    """Return the 3-vector of a skew-symmetric matrix's independent entries."""
    return np.array([
        skew[2, 1] - skew[1, 2],
        skew[0, 2] - skew[2, 0],
        skew[1, 0] - skew[0, 1],
    ])


def _log_map(rotation: np.ndarray) -> np.ndarray:
    """Return the rotation vector (axis * angle) of a 3x3 rotation matrix."""
    cos_theta = float(np.clip((np.trace(rotation) - 1.0) / 2.0, -1.0, 1.0))
    theta = float(np.arccos(cos_theta))
    if theta < 1e-8:
        return 0.5 * _vee(rotation - rotation.T)
    return theta * _vee(rotation - rotation.T) / (2.0 * np.sin(theta))


def _error(
    q: np.ndarray,
    position: np.ndarray,
    rotation: np.ndarray,
    fk: Callable[[np.ndarray], tuple[np.ndarray, np.ndarray]],
    joint_adrs: Sequence[int],
) -> np.ndarray:
    """Return the six-vector world-frame task error at the arm configuration ``q``."""
    pos_current, rot_current = fk(q)
    return np.concatenate([
        position - np.asarray(pos_current, dtype=float),
        _log_map(np.asarray(rotation, dtype=float) @ np.asarray(rot_current).T),
    ])


def _descent(
    q: np.ndarray,
    position: np.ndarray,
    rotation: np.ndarray,
    fk: Callable[[np.ndarray], tuple[np.ndarray, np.ndarray]],
    joint_adrs: Sequence[int],
    lower: np.ndarray,
    upper: np.ndarray,
) -> tuple[np.ndarray, bool]:
    """Run one damped-least-squares descent; return ``(q, converged)``."""
    count = q.size
    damping = _LAMBDA0
    error = _error(q, position, rotation, fk, joint_adrs)
    for _ in range(_MAX_ITERS):
        if (np.linalg.norm(error[:3]) <= _TOL
                and np.linalg.norm(error[3:]) <= _TOL):
            return q, True
        jacobian = np.zeros((6, count))
        for column in range(count):
            perturbed = q.copy()
            perturbed[column] += _EPS
            jacobian[:, column] = (
                _error(perturbed, position, rotation, fk, joint_adrs) - error) / _EPS
        gain = jacobian @ jacobian.T + damping * damping * np.eye(6)
        step = -jacobian.T @ np.linalg.solve(gain, error)
        candidate = np.clip(q + step, lower, upper)
        candidate_error = _error(candidate, position, rotation, fk, joint_adrs)
        if np.linalg.norm(candidate_error) < np.linalg.norm(error):
            q, error = candidate, candidate_error
            damping = max(damping / 2.0, _LAMBDA_MIN)
        else:
            damping *= 2.0
    return q, False


def solve_ik(
    position: np.ndarray,
    rotation: np.ndarray,
    *,
    fk: Callable[[np.ndarray], tuple[np.ndarray, np.ndarray]],
    joint_adrs: Sequence[int],
    lower: np.ndarray,
    upper: np.ndarray,
    restore: Callable[[], None],
) -> np.ndarray | None:
    """Solve for the arm joint values putting the gripper at ``position``/``rotation``.

    ``fk(q)`` must return ``(position, rotation_matrix)`` for the arm's five
    joints, expressed in the world frame; ``joint_adrs`` are those joints' qpos
    slots in ``mjData``; ``lower``/``upper`` are their travel limits; and
    ``restore()`` puts ``mjData`` back exactly as it was found (the caller's
    snapshot + one ``mj_forward``).

    Returns the converged joint vector, or ``None`` when no start converges --
    which the backend reads as "this pose is not reachable" (status.md R3).
    ``mjData`` is restored on every exit path.
    """
    position = np.asarray(position, dtype=float)
    rotation = np.asarray(rotation, dtype=float)
    lower = np.asarray(lower, dtype=float)
    upper = np.asarray(upper, dtype=float)
    count = len(joint_adrs)

    try:
        generator = np.random.default_rng(_SEED)
        starts = [np.zeros(count)]
        starts.extend(
            generator.uniform(lower, upper) for _ in range(_RANDOM_STARTS))
        for start in starts:
            solved, converged = _descent(
                np.asarray(start, dtype=float), position, rotation, fk,
                joint_adrs, lower, upper)
            if converged:
                return solved
        return None
    finally:
        restore()
