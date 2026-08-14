#!/usr/bin/env bash
# start-feature.sh — on-demand dispatch for one feature worktree.
#
# Creates a fresh worktree off the latest origin/main for a GitHub issue and
# launches a DETACHED Claude Code "worktree manager" (the /run-feature loop) that
# drives it to ready-for-merge. Invoked by Sisyphus (Pi) over SSH; returns in
# seconds so the manager outlives the SSH connection (it runs inside tmux).
#
# Pull-based: work is only ever started by an explicit call to this script.
# There is intentionally NO backfill cron on the laptop.
#
# Usage:   start-feature.sh <issue-number> [slug]
# Env:     DRY_RUN=1        print the plan and the exact claude command, do nothing
#          SISYPHUS_MODEL   manager model alias (default: opus)
#          SISYPHUS_MAIN    path to the main worktree (default: ~/worktrees/main)
#          SISYPHUS_WT_ROOT worktree parent dir     (default: ~/worktrees)
set -euo pipefail

# --- tools must be found under a non-interactive SSH shell ---
export PATH="$HOME/.local/bin:$HOME/.pixi/bin:$PATH"

REPO_MAIN="${SISYPHUS_MAIN:-$HOME/worktrees/main}"
WT_ROOT="${SISYPHUS_WT_ROOT:-$HOME/worktrees}"
MODEL="${SISYPHUS_MODEL:-opus}"
DRY_RUN="${DRY_RUN:-0}"

die() { echo "start-feature: $*" >&2; exit 1; }

ISSUE="${1:-}"
SLUG_ARG="${2:-}"
[ -n "$ISSUE" ] || die "usage: start-feature <issue-number> [slug]"
printf '%s' "$ISSUE" | grep -qE '^[0-9]+$' || die "issue must be a number, got: '$ISSUE'"

command -v claude >/dev/null 2>&1 || die "claude not found on PATH"
command -v gh     >/dev/null 2>&1 || die "gh not found on PATH"
command -v tmux   >/dev/null 2>&1 || die "tmux not found on PATH"
command -v git    >/dev/null 2>&1 || die "git not found on PATH"

cd "$REPO_MAIN" 2>/dev/null || die "main worktree not found at $REPO_MAIN"

# --- verify the issue exists and is open (the issue body IS the brief) ---
state="$(gh issue view "$ISSUE" --json state -q '.state' 2>/dev/null)" \
  || die "issue #$ISSUE not found (gh issue view failed)"
title="$(gh issue view "$ISSUE" --json title -q '.title' 2>/dev/null)"
[ -n "$state" ] || die "could not read issue state"
[ "$state" = "OPEN" ] || die "issue #$ISSUE is $state, not OPEN — refusing to dispatch"

# --- derive slug: iN-<title-slug>, or iN-<explicit slug> ---
if [ -n "$SLUG_ARG" ]; then
  base="$SLUG_ARG"
else
  base="$(printf '%s' "$title" | tr '[:upper:]' '[:lower:]' \
        | sed -E 's/[^a-z0-9]+/-/g; s/^-+//; s/-+$//' | cut -c1-40 | sed -E 's/-+$//')"
fi
[ -n "$base" ] || base="feature"
slug="i${ISSUE}-${base}"
branch="feat/$slug"
wt="$WT_ROOT/$slug"
tmux_name="feat-$slug"
ts="$(date +%Y%m%d-%H%M%S)"
logdir="$wt/.dev/runs/$slug/$ts"
log="$logdir/manager.log"

# --- reap a COMPLETED run's leftover tmux session ---
# A finished run's session lingers on purpose (the inner command ends with
# `exec bash` so scripts/pi/watch-run.sh can read the `EXIT=<code>` marker before
# the session dies); watch-run.sh kills it once consumed. When that teardown never
# happens (no watcher, SSH down) the idle shell used to block re-dispatch of the
# slug forever. Reap it here instead — only a genuinely LIVE run is refused.
run_finished() {  # $1 = tmux session name; reads $wt / $slug
  local f pane sid busy
  f="$(ls -1t "$wt"/.dev/runs/"$slug"/*/*.log 2>/dev/null | head -1 || true)"
  if [ -n "$f" ]; then
    # definitive: the launcher echoes `EXIT=<code> (<date>)` as the run's last
    # log line. ANCHORED on purpose — a live agent's own bash steps echo
    # `EXIT=$?` into this same log (inside JSON, never at column 0), and the
    # loose form would read a running run as finished and reap it. Same pattern
    # as scripts/pi/watch-run.sh; both are held to it by
    # scripts/tests/test_run_completion_marker.py.
    grep -qE '^EXIT=[0-9]+ \(' "$f"
    return
  fi
  # no run log (worktree already cleaned up): a finished session is just the idle
  # shell, while a live one still has the agent (claude/node/pixi/...) under it
  for pane in $(tmux list-panes -t "$1" -F '#{pane_pid}' 2>/dev/null || true); do
    sid="$(ps -o sid= -p "$pane" 2>/dev/null | tr -d ' ')"
    [ -n "$sid" ] || continue
    busy="$(ps -s "$sid" -o comm= 2>/dev/null | grep -vxE 'bash|sh|ps|grep' || true)"
    if [ -n "$busy" ]; then return 1; fi
  done
  return 0
}

if tmux has-session -t "$tmux_name" 2>/dev/null; then
  if run_finished "$tmux_name"; then
    if [ "$DRY_RUN" = "1" ]; then
      echo "[dry-run] would reap completed run's stale tmux session: $tmux_name"
    else
      echo "reaping completed run's stale tmux session: $tmux_name"
      tmux kill-session -t "$tmux_name" 2>/dev/null || true
    fi
  else
    die "tmux session already running: $tmux_name"
  fi
fi

# --- refuse to clobber existing work ---
[ -e "$wt" ] && die "worktree path already exists: $wt"
git show-ref --verify --quiet "refs/heads/$branch" && die "branch already exists: $branch"

# the command the detached manager will run
inner="export PATH=\"\$HOME/.local/bin:\$HOME/.pixi/bin:\$PATH\"; \
export CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0; \
cd '$wt'; \
mkdir -p '$logdir'; \
echo '[bootstrap] pixi install (provision env before first agent action)...' | tee -a '$log'; \
pixi install >>'$log' 2>&1 || echo '[bootstrap] pixi install returned nonzero; manager will surface it' | tee -a '$log'; \
echo '[bootstrap] pixi run install-openclaw (node/ is per-worktree and gitignored; robot_brain tests need it)...' | tee -a '$log'; \
pixi run install-openclaw >>'$log' 2>&1 || echo '[bootstrap] install-openclaw returned nonzero; robot_brain OpenClaw tests will fail until it is rerun' | tee -a '$log'; \
claude --model '$MODEL' --permission-mode bypassPermissions \
  --output-format stream-json --verbose \
  -p '/run-feature $ISSUE' 2>&1 | tee -a '$log'; \
echo \"EXIT=\${PIPESTATUS[0]} (\$(date))\" | tee -a '$log'; \
exec bash"

if [ "$DRY_RUN" = "1" ]; then
  echo "[dry-run] issue #$ISSUE — $title"
  echo "[dry-run] branch:   $branch"
  echo "[dry-run] worktree: $wt   (from origin/main)"
  echo "[dry-run] tmux:     $tmux_name"
  echo "[dry-run] log:      $log"
  echo "[dry-run] manager command:"
  echo "  CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0 claude --model $MODEL --permission-mode bypassPermissions --output-format stream-json --verbose -p '/run-feature $ISSUE'"
  exit 0
fi

# --- create the worktree off the latest origin/main ---
git fetch origin --quiet
git worktree add "$wt" -b "$branch" origin/main >/dev/null \
  || die "git worktree add failed"

# --- launch the detached manager ---
tmux new-session -d -s "$tmux_name" -c "$wt" bash -c "$inner" \
  || { git worktree remove --force "$wt"; git branch -D "$branch" 2>/dev/null || true; die "tmux launch failed — rolled back worktree"; }

echo "started: issue #$ISSUE — $title"
echo "  branch:   $branch"
echo "  worktree: $wt"
echo "  tmux:     $tmux_name"
echo "  attach:   ssh laptop -t \"tmux attach -t $tmux_name\""
echo "  log:      $log"
