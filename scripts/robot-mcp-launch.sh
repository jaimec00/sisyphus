#!/usr/bin/env bash
# robot-mcp-launch.sh — start the robot MCP server with every workspace package
# on PYTHONPATH, discovered from the source tree instead of hand-listed.
#
# Why this exists: the deploy path used to carry a hand-written
# `PYTHONPATH=<repo>/src/a:<repo>/src/b:...` list (in the OpenClaw config, in a
# README, in a test asserting the list). #54 added `robot_world`, every one of
# those lists stayed at four entries, and the server died with
# `ModuleNotFoundError` the moment it was launched for real — while `pixi run
# test` stayed green, because colcon builds every package from its manifest and
# never reads the launch string. Discovery removes the second source of truth:
# a package added tomorrow is on the path with no edit anywhere.
#
# The environment is somebody else's job. This script assumes it is already
# running inside the pixi env and does no `pixi run` of its own, and it does not
# sniff for one either — pixi supplies the environment, this supplies discovery,
# and keeping them separate is what lets the gate's boot-smoke run the identical
# code path the deployment runs. So the deployed command wraps it:
#
#   ssh -T laptop bash -lc 'exec pixi run --frozen \
#     --manifest-path <repo>/pixi.toml <repo>/scripts/robot-mcp-launch.sh'
#
# Trailing arguments are forwarded to the server (`--world-state PATH`, ...).
set -euo pipefail

die() { echo "robot-mcp-launch.sh: $*" >&2; exit 1; }

# The repo root is this script's own parent directory, resolved *lexically* —
# `readlink -f` is deliberately absent. The path bash was invoked with is the
# repo we were asked to launch; following symlinks would silently relaunch some
# other checkout instead. That is also what makes this testable: the boot-smoke
# stands up a fake repo root whose `scripts/robot-mcp-launch.sh` is a symlink to
# this file and whose `src/<pkg>` are symlinks to real packages, and drops one
# of them to prove discovery bites. Under `readlink -f` that test would resolve
# back to the real tree, find every package, and pass while broken.
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source_dir="$repo_root/src"

# A `package.xml` marks a package root — the same criterion colcon uses, and the
# same one `scripts/check_test_integrity.py` audits against. No allowlist, no
# "only the ones the server imports": a filter is a hand-maintained list wearing
# a different hat.
discovered=()
for manifest in "$source_dir"/*/package.xml; do
  [ -f "$manifest" ] || continue
  discovered+=("$(dirname "$manifest")")
done

[ "${#discovered[@]}" -gt 0 ] ||
  die "no package.xml found under $source_dir — refusing to launch with an" \
      "empty discovery result (is $repo_root really the repository root?)"

# Discovered packages first, then whatever the caller already had: an inherited
# PYTHONPATH is appended, never clobbered, so a caller can add to the path but
# cannot silently shadow a workspace package with a stale copy.
discovered_path="$(IFS=:; echo "${discovered[*]}")"
export PYTHONPATH="${discovered_path}${PYTHONPATH:+:$PYTHONPATH}"

exec python -m robot_mcp "$@"
