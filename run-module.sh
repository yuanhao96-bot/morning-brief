#!/bin/bash
set -euo pipefail

# Wrapper invoked by launchd to run a morning-brief skill non-interactively,
# then autocommit and push any tracked files the module produced.
#
# Usage: run-module.sh <module>
# Modules: radar | ingest | digest
#
# Module chain (executed in order each morning):
#   radar  → drops new articles into the corpus
#   ingest → reads everything new in the corpus, updates the wiki
#   digest → synthesizes today's wiki additions into a daily brief
# Each stage execs the next on success, so launchd only triggers `radar`.

MODULE="$1"
TWIN_DIR="$HOME/projects/morning-brief"
LOG="$TWIN_DIR/cron.log"

# launchd gives us a minimal PATH; restore the homebrew + system locations
# we actually need (claude, git, ssh).
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

CLAUDE_BIN="/opt/homebrew/bin/claude"
GIT_BIN="/opt/homebrew/bin/git"

# Tracked paths each module is allowed to commit, and the tool allow-list
# the inner `claude --print` invocation needs. Anything not listed here is
# left alone, so the autocommit can never sweep up stray junk and the
# headless claude run can never escalate beyond its scoped permissions.
case "$MODULE" in
  radar)
    COMMIT_PATHS=("extracts/radar/state.yaml")
    ALLOWED_TOOLS=(Read Write Edit Glob Grep WebFetch WebSearch Bash)
    ;;
  ingest)
    # All ingest outputs are gitignored: extracts/ingest/*.yaml is regenerable
    # and wiki/ is Syncthing-managed (twin → user). Nothing to commit.
    COMMIT_PATHS=()
    ALLOWED_TOOLS=(Read Write Edit Glob Grep "Bash(shasum:*)")
    ;;
  digest)
    # Digest writes to wiki/digests/, which is gitignored (Syncthing-managed).
    COMMIT_PATHS=()
    ALLOWED_TOOLS=(Read Write Edit Glob Grep)
    ;;
  *)
    echo "$(date -Iseconds) | unknown module: $MODULE" >> "$LOG"
    exit 2
    ;;
esac

echo "$(date -Iseconds) | Starting $MODULE" >> "$LOG"
cd "$TWIN_DIR"

# --allowedTools is variadic and will consume any trailing positional
# arguments (including the prompt) as additional tool names. To avoid
# that, we pass the tool list as a CSV string AND feed the prompt via
# stdin instead of a trailing positional, so there's nothing for the
# variadic flag to greedily eat.
ALLOWED_CSV=$(IFS=,; echo "${ALLOWED_TOOLS[*]}")

if ! echo "Run the $MODULE skill as defined in skills/$MODULE/SKILL.md" \
   | "$CLAUDE_BIN" --print --allowedTools "$ALLOWED_CSV" \
     >> "$LOG" 2>&1; then
  echo "$(date -Iseconds) | $MODULE FAILED — skipping commit/push" >> "$LOG"
  exit 1
fi

if [ ${#COMMIT_PATHS[@]} -gt 0 ]; then
  "$GIT_BIN" add -- "${COMMIT_PATHS[@]}" 2>> "$LOG" || true
  if ! "$GIT_BIN" diff --cached --quiet; then
    "$GIT_BIN" commit -m "chore($MODULE): autocommit $(date -I) run" >> "$LOG" 2>&1
    if ! "$GIT_BIN" push >> "$LOG" 2>&1; then
      echo "$(date -Iseconds) | $MODULE push failed (commit kept locally)" >> "$LOG"
    fi
  fi
fi

echo "$(date -Iseconds) | Finished $MODULE" >> "$LOG"

# Module chain: radar → ingest → digest. Each stage execs the next on
# success, so the launchd-tracked PID becomes the downstream stage cleanly
# (no nested process stack). Failures short-circuit at the earlier `exit 1`
# so downstream stages never run on bad upstream output.
case "$MODULE" in
  radar)  NEXT=ingest ;;
  ingest) NEXT=digest ;;
  *)      NEXT="" ;;
esac

if [ -n "$NEXT" ]; then
  echo "$(date -Iseconds) | Chaining $MODULE → $NEXT" >> "$LOG"
  exec "$0" "$NEXT"
fi
