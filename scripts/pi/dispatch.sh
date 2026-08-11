#!/usr/bin/env bash
# dispatch.sh — Pi-side entrypoint: start a worktree run on the laptop AND
# launch the push-watcher so Sisyphus is woken the instant the run ends.
#
# This is the push-era replacement for "dispatch, then let the minute-cron
# discover the PR". The launcher on the laptop still runs the manager/op agent
# detached in tmux; dispatch.sh additionally spawns watch-run.sh (Pi-side,
# detached) which waits for that tmux session to end and fires the wake.
#
# Usage:
#   dispatch.sh feature <issue-number> [slug]
#   dispatch.sh op <slug> -f <pi-side-brief-file>
#   dispatch.sh op <slug> "<change prompt>"
#
# Env: DRY_RUN=1 forwarded to the launcher (prints plan, starts nothing, no watcher).
set -euo pipefail

BIN="$(cd "$(dirname "$0")" && pwd)"
LAPTOP="laptop"
LAUNCHERS="~/worktrees/main/scripts"

KIND="${1:?usage: dispatch.sh <feature|op> ...}"; shift

fire_watcher() {  # $1=out  $2=kind
  local out="$1" kind="$2" branch tmux slug
  branch="$(printf '%s\n' "$out" | sed -nE 's/^[[:space:]]*branch:[[:space:]]*//p' | head -1)"
  tmux="$(printf '%s\n'   "$out" | sed -nE 's/^[[:space:]]*tmux:[[:space:]]*//p'   | head -1)"
  if [ -z "$tmux" ] || [ -z "$branch" ]; then
    echo "dispatch: WARNING could not parse tmux/branch from launcher output — no watcher launched (poll backstop still applies)" >&2
    return 0
  fi
  # tmux is feat-<slug> or op-<slug>; strip the leading kind prefix for slug
  slug="${tmux#feat-}"; slug="${slug#op-}"
  nohup "$BIN/watch-run.sh" "$kind" "$slug" "$tmux" "$branch" >/dev/null 2>&1 &
  echo "watcher: launched (pid $!) tmux=$tmux branch=$branch slug=$slug"
}

case "$KIND" in
  feature)
    [ "$#" -ge 1 ] || { echo "usage: dispatch.sh feature <issue-number> [slug]" >&2; exit 1; }
    OUT="$(ssh "$LAPTOP" "DRY_RUN=${DRY_RUN:-0} $LAUNCHERS/start-feature.sh $*")"
    echo "$OUT"
    [ "${DRY_RUN:-0}" = "1" ] || fire_watcher "$OUT" feature
    ;;
  op)
    SLUG="${1:?usage: dispatch.sh op <slug> (-f <brief-file> | \"<prompt>\")}"; shift
    if [ "${1:-}" = "-f" ]; then
      BRIEF_FILE="${2:?-f needs a Pi-side brief file}"
      [ -f "$BRIEF_FILE" ] || { echo "dispatch: brief file not found: $BRIEF_FILE" >&2; exit 1; }
      REMOTE="/tmp/op-brief-${SLUG}-$(date +%s).md"
      scp -q "$BRIEF_FILE" "$LAPTOP:$REMOTE"
      OUT="$(ssh "$LAPTOP" "DRY_RUN=${DRY_RUN:-0} $LAUNCHERS/start-op.sh '$SLUG' -f '$REMOTE'")"
    else
      PROMPT="${1:?op needs a prompt or -f <file>}"
      # pass prompt via a heredoc-safe base64 hop to avoid quoting hell over ssh
      B64="$(printf '%s' "$PROMPT" | base64 | tr -d '\n')"
      REMOTE="/tmp/op-brief-${SLUG}-$(date +%s).md"
      ssh "$LAPTOP" "echo '$B64' | base64 -d > '$REMOTE'"
      OUT="$(ssh "$LAPTOP" "DRY_RUN=${DRY_RUN:-0} $LAUNCHERS/start-op.sh '$SLUG' -f '$REMOTE'")"
    fi
    echo "$OUT"
    [ "${DRY_RUN:-0}" = "1" ] || fire_watcher "$OUT" op
    ;;
  *)
    echo "dispatch: unknown kind '$KIND' (want: feature|op)" >&2; exit 1 ;;
esac
