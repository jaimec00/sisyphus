#!/usr/bin/env bash
# start-op.sh — on-demand dispatch for one OPERATIONAL (bypass-the-loop) change.
#
# Operational/meta changes — briefs, docs, agent-rule / .claude tweaks, ops
# tooling — no longer go through the full feature loop and are no longer authored
# or merged by Sisyphus directly. Sisyphus writes a change prompt and dispatches a
# DETACHED Claude Code "operational agent" (the /run-op loop) that authors the
# change, opens a PR, and squash-merges it. Mirrors start-feature.sh.
#
# Usage:   start-op.sh <slug> "<change prompt>"
#          start-op.sh <slug> -f <brief-file>
# Env:     DRY_RUN=1        print the plan and the exact claude command, do nothing
#          SISYPHUS_MODEL   agent model alias (default: opus)
#          SISYPHUS_MAIN    path to the main worktree (default: ~/worktrees/main)
#          SISYPHUS_WT_ROOT worktree parent dir     (default: ~/worktrees)
set -euo pipefail

export PATH="$HOME/.local/bin:$HOME/.pixi/bin:$PATH"

REPO_MAIN="${SISYPHUS_MAIN:-$HOME/worktrees/main}"
WT_ROOT="${SISYPHUS_WT_ROOT:-$HOME/worktrees}"
MODEL="${SISYPHUS_MODEL:-opus}"
DRY_RUN="${DRY_RUN:-0}"

die() { echo "start-op: $*" >&2; exit 1; }

SLUG_ARG="${1:-}"
[ -n "$SLUG_ARG" ] || die "usage: start-op <slug> \"<change prompt>\"  (or: <slug> -f <brief-file>)"

# --- gather the brief text (argv or -f file) ---
if [ "${2:-}" = "-f" ]; then
  BRIEF_FILE="${3:-}"
  { [ -n "$BRIEF_FILE" ] && [ -f "$BRIEF_FILE" ]; } || die "brief file not found: ${BRIEF_FILE:-<none>}"
  BRIEF="$(cat "$BRIEF_FILE")"
else
  BRIEF="${2:-}"
fi
[ -n "$BRIEF" ] || die "empty change prompt — nothing to do"

command -v claude >/dev/null 2>&1 || die "claude not found on PATH"
command -v gh     >/dev/null 2>&1 || die "gh not found on PATH"
command -v tmux   >/dev/null 2>&1 || die "tmux not found on PATH"
command -v git    >/dev/null 2>&1 || die "git not found on PATH"

cd "$REPO_MAIN" 2>/dev/null || die "main worktree not found at $REPO_MAIN"

# --- derive slug: op-<slug> ---
base="$(printf '%s' "$SLUG_ARG" | tr '[:upper:]' '[:lower:]' \
      | sed -E 's/[^a-z0-9]+/-/g; s/^-+//; s/-+$//' | cut -c1-40 | sed -E 's/-+$//')"
[ -n "$base" ] || die "slug reduces to empty"
slug="op-${base}"
branch="ops/$slug"
wt="$WT_ROOT/$slug"
tmux_name="op-$slug"
ts="$(date +%Y%m%d-%H%M%S)"
logdir="$wt/.dev/runs/$slug/$ts"
log="$logdir/agent.log"

# --- refuse to clobber existing work ---
[ -e "$wt" ] && die "worktree path already exists: $wt"
git show-ref --verify --quiet "refs/heads/$branch" && die "branch already exists: $branch"
tmux has-session -t "$tmux_name" 2>/dev/null && die "tmux session already running: $tmux_name"

# the command the detached agent will run
inner="export PATH=\"\$HOME/.local/bin:\$HOME/.pixi/bin:\$PATH\"; \
export CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0; \
cd '$wt'; \
mkdir -p '$logdir'; \
claude --model '$MODEL' --permission-mode bypassPermissions \
  --output-format stream-json --verbose \
  -p '/run-op' 2>&1 | tee '$log'; \
echo \"EXIT=\${PIPESTATUS[0]} (\$(date))\" | tee -a '$log'; \
exec bash"

if [ "$DRY_RUN" = "1" ]; then
  echo "[dry-run] operational change — slug: $slug"
  echo "[dry-run] branch:   $branch"
  echo "[dry-run] worktree: $wt   (from origin/main)"
  echo "[dry-run] tmux:     $tmux_name"
  echo "[dry-run] log:      $log"
  echo "[dry-run] brief:"
  printf '%s\n' "$BRIEF" | sed 's/^/    /'
  echo "[dry-run] agent command:"
  echo "  CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0 claude --model $MODEL --permission-mode bypassPermissions --output-format stream-json --verbose -p '/run-op'"
  exit 0
fi

# --- create the worktree off the latest origin/main ---
git fetch origin --quiet
git worktree add "$wt" -b "$branch" origin/main >/dev/null \
  || die "git worktree add failed"

# --- write the brief into the worktree (gitignored .dev/) for /run-op to read ---
mkdir -p "$wt/.dev"
printf '%s\n' "$BRIEF" > "$wt/.dev/op-brief.md"

# --- launch the detached agent ---
tmux new-session -d -s "$tmux_name" -c "$wt" bash -c "$inner" \
  || { git worktree remove --force "$wt"; git branch -D "$branch" 2>/dev/null || true; die "tmux launch failed — rolled back worktree"; }

echo "started: operational change — $slug"
echo "  branch:   $branch"
echo "  worktree: $wt"
echo "  tmux:     $tmux_name"
echo "  attach:   ssh laptop -t \"tmux attach -t $tmux_name\""
echo "  log:      $log"
