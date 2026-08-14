#!/usr/bin/env bash
# watch-run.sh — Pi-side PUSH notifier for a dispatched worktree run.
#
# Replaces minute-polling: waits (over the already-proven Pi->laptop SSH link)
# for a run's tmux session on the laptop to END, then fires ONE wake to Sisyphus
# via the LOCAL Gateway so merge evaluation happens on push (~15s), not on a
# fixed poll cadence. The wake turn does all the gh/merge work itself.
#
# Why Pi-side: the laptop has no `openclaw` CLI and Sisyphus's Gateway binds
# loopback-only, so the launcher on the laptop cannot reach it. The Pi can:
# gh is authed here and the Gateway is local. So the Pi watches and wakes.
#
# Usage: watch-run.sh <feature|op> <slug> <tmux-session> <branch>
# Notes: launched detached (nohup) by dispatch.sh; one watcher per run.
set -uo pipefail

KIND="${1:?usage: watch-run.sh <feature|op> <slug> <tmux> <branch>}"
SLUG="${2:?slug}"
TMUX="${3:?tmux session}"
BRANCH="${4:?branch}"

LAPTOP="laptop"
REPO="jaimec00/sisyphus"
SESSION_ID="runwake-${SLUG}-$(date +%Y%m%d-%H%M%S)"
CHAT="8912295558"
SELF_LOG="$HOME/sisyphus/watch/${SLUG}.watch.log"

mkdir -p "$(dirname "$SELF_LOG")"
log() { echo "[$(date '+%F %T')] $*" >>"$SELF_LOG"; }

log "watch start: kind=$KIND slug=$SLUG tmux=$TMUX branch=$BRANCH"

# 1) Block until the run finishes. The launcher ends its inner command with
#    `echo EXIT=<code> (<date>)` then `exec bash`, so the tmux session LINGERS as
#    an idle shell after the run finishes — session-death is NOT the completion
#    signal. The real signal is that marker line in the run log. Poll for it;
#    fall back to session-death only for hard kills (OOM/kill -9/reboot) that
#    never write the marker.
#
#    The match is ANCHORED (`^EXIT=<digits> (`) and that anchor is load-bearing.
#    A running agent's own bash steps routinely `echo "EXIT=$?"`, and that text
#    lands in this same stream-json log embedded in a JSON string
#    (`"content":"EXIT=0\n..."`) — never at column 0. The old loose
#    `EXIT=[0-9]+` matched those echoes and declared a still-running op finished,
#    which since PR #72 also REAPS its live tmux session (that killed
#    `ops/op-pkgxml-validate` mid-build, losing uncommitted work). Only the
#    wrapper writes a raw line starting at column 0, so only the wrapper can
#    satisfy the anchored form. scripts/tests/test_run_completion_marker.py
#    holds this pattern to that behavior.
attempt=0
while true; do
  DONE="$(ssh -o ServerAliveInterval=30 -o ServerAliveCountMax=4 -o ConnectTimeout=20 "$LAPTOP" \
    "f=\$(ls -1t ~/worktrees/'$SLUG'/.dev/runs/'$SLUG'/*/*.log 2>/dev/null | head -1); \
     [ -n \"\$f\" ] && grep -qE '^EXIT=[0-9]+ \(' \"\$f\" && echo done" 2>>"$SELF_LOG" || true)"
  if [ "$DONE" = "done" ]; then
    break
  fi
  if ! ssh -o ConnectTimeout=20 "$LAPTOP" "tmux has-session -t '$TMUX' 2>/dev/null"; then
    attempt=$((attempt+1))
    log "session gone with no EXIT marker (attempt $attempt) — likely hard kill; waking anyway"
    break
  fi
  sleep 15
done
log "run finished (EXIT marker or hard-kill detected)"

# 2) Best-effort: read the run's EXIT code line from its log (context only).
#    Anchored for the same reason as step 1 — unanchored, this reports the exit
#    code of whatever the agent last echoed rather than the run's own.
EXIT_INFO="$(ssh -o ConnectTimeout=20 "$LAPTOP" \
  "f=\$(ls -1t ~/worktrees/'$SLUG'/.dev/runs/'$SLUG'/*/*.log 2>/dev/null | head -1); \
   [ -n \"\$f\" ] && grep -oE '^EXIT=[0-9]+' \"\$f\" | tail -1" 2>/dev/null || true)"
[ -n "$EXIT_INFO" ] || EXIT_INFO="EXIT=unknown"
log "exit info: $EXIT_INFO"

# 3) Fire a THIN wake on the LOCAL Gateway, delivered to Telegram via the sisyphus
#    account. The wake carries no merge policy — it points Sisyphus at the single
#    canonical policy doc in the repo, which the wake turn reads and follows.
read -r -d '' MSG <<EOF || true
[push] Worktree run just finished — evaluate it for merge now (do NOT wait for the poll).
Run: kind=$KIND slug=$SLUG branch=$BRANCH ($EXIT_INFO). Repo: $REPO.

Find the PR for branch \`$BRANCH\` (\`gh pr list --head $BRANCH --state all\`), then
evaluate and act per the canonical merge policy at \`.claude/commands/run-merge-eval.md\`
in $REPO. Read that file — it is the single source of truth for merge authority, how to
judge a PR, operational scope, the merge steps, when to escalate, and the NO_REPLY
silence contract. Do NOT re-run tests.
EOF

log "firing wake session_id=$SESSION_ID"
if openclaw agent --agent sisyphus --session-id "$SESSION_ID" \
     --message "$MSG" --deliver --reply-channel telegram --reply-account sisyphus --reply-to "$CHAT" \
     --thinking off --json >>"$SELF_LOG" 2>&1; then
  log "wake fired ok"
else
  log "wake FAILED (nonzero) — backstop cron will still catch this PR"
fi

# 4) Tear down the now-CONSUMED tmux session. The `exec bash` tail keeps a finished
#    run's session alive so step 1 can read its EXIT marker — but once the wake has
#    been fired nothing else needs it, and left alone those idle shells pile up and
#    get misread as still-running managers. Reap regardless of wake success: the
#    backstop cron queries gh directly and never looks at the session. On the
#    hard-kill branch the session is already gone, so this is a harmless no-op.
log "tearing down consumed tmux session: $TMUX"
ssh -o ConnectTimeout=20 "$LAPTOP" "tmux kill-session -t '$TMUX' 2>/dev/null || true" >>"$SELF_LOG" 2>&1 \
  || log "teardown ssh failed — session may linger until the next dispatch of this slug reaps it"
