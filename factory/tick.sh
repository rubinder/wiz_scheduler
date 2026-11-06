#!/bin/bash
# One factory tick. launchd runs this every 30 minutes; safe to run by hand.
# Usage: tick.sh [--dry-run]
# The claude prompt is a fixed string; nothing from GitHub reaches this script's command lines.
set -uo pipefail

FACTORY_HOME="${FACTORY_HOME:-$HOME/factory}"
APP="$FACTORY_HOME/wiz_scheduler"
MKT="$FACTORY_HOME/wiz_scheduler_marketing"
PY="$APP/backend/.venv/bin/python"
LOGDIR="$HOME/Library/Logs/wizfactory"
LOG="$LOGDIR/$(date -u +%Y-%m-%d).log"
TICK_TIMEOUT_S="${FACTORY_TICK_TIMEOUT_S:-10800}"
# Overridable only so the tests can stub it; launchd never sets it.
CLAUDE_BIN="${FACTORY_CLAUDE_BIN:-claude}"
# A dry run writes nothing but the lock and the log: no clone sync, no pause.
DRY=0
[[ "${1:-}" == "--dry-run" ]] && DRY=1
mkdir -p "$LOGDIR"
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$HOME/.claude/local:/Applications/Docker.app/Contents/Resources/bin:$PATH"
log() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $*" >> "$LOG"; }
# macOS has no timeout(1). Run the command in its own process group; after N
# seconds TERM the whole group, then KILL whatever is left, and exit 142. Killing
# the group (not just the command) takes background servers and other orphans with it.
TIMEOUT_PL='
  use POSIX ":sys_wait_h";
  my $t = shift;
  my $pid = fork; defined $pid or exit 127;
  if ($pid == 0) { setpgrp(0, 0); exec @ARGV or exit 127 }
  setpgrp($pid, $pid);
  $SIG{ALRM} = sub {
    kill "TERM", -$pid;
    for (1 .. 10) { waitpid($pid, WNOHANG); last unless kill 0, -$pid; sleep 1 }
    kill "KILL", -$pid; waitpid($pid, 0); exit 142;
  };
  alarm $t;
  waitpid($pid, 0); my $s = $?;
  exit($s & 127 ? 128 + ($s & 127) : $s >> 8);
'
with_timeout() { perl -e "$TIMEOUT_PL" "$@"; }

cd "$APP" || { log "no app clone at $APP"; exit 0; }

# A stopped factory is left exactly as it is: no lock, no sync.
[[ -e "$FACTORY_HOME/STOP" ]] && { log "skip: STOP file present"; exit 0; }

exec 9>"$FACTORY_HOME/tick.lock"
flock -n 9 2>/dev/null || /usr/bin/python3 -c 'import fcntl,sys; fcntl.flock(9, fcntl.LOCK_EX|fcntl.LOCK_NB)' || { log "skip: lock"; exit 0; }

# Newest main in both clones before preflight, so a fix to preflight itself
# lands even while every tick is skipping. Only after the lock is ours (a
# running tick's clone is never reset under it) and only in the real clones.
sync_clone() {
  local dir="$1" top want
  want=$(cd "$dir" 2>/dev/null && pwd -P) || { log "skip: no clone at $dir"; return 1; }
  top=$(git -C "$dir" rev-parse --show-toplevel 2>/dev/null)
  [[ -n "$top" && "$top" == "$want" ]] || { log "skip: $dir is not its own git toplevel"; return 1; }
  (cd "$dir" && with_timeout 120 git fetch -q origin 9>&- && git checkout -q main && git reset -q --hard origin/main) \
    || { log "skip: sync failed in $dir"; return 1; }
}
if [[ $DRY -eq 0 ]]; then
  sync_clone "$APP" || exit 0
  if [[ -d "$MKT" ]]; then sync_clone "$MKT" || exit 0; fi
else
  log "dry run: clones not synced"
fi

# The lock is ours, so preflight must not report it as held by another tick.
reasons=$(FACTORY_TICK_HOLDS_LOCK=1 "$PY" -m factory.preflight check 9>&-); rc=$?
if [[ $rc -ne 0 ]]; then   # run only on exactly 0; any other code is a skip
  log "skip (rc=$rc): $reasons"; exit 0
fi

PROMPT="/factory-tick"
[[ $DRY -eq 1 ]] && PROMPT="/factory-tick --dry-run"
OUT=$(mktemp "${TMPDIR:-/tmp}/wizfactory.XXXXXX")
trap 'rm -f "$OUT"' EXIT
log "run: $PROMPT"
# 9>&-: the tick lock stays with this script. Nothing claude starts (gate
# servers, stray background jobs) inherits it, so an orphan can never hold the
# lock and stop every later tick.
CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0 caffeinate -i \
  perl -e "$TIMEOUT_PL" "$TICK_TIMEOUT_S" \
  "$CLAUDE_BIN" -p --permission-mode bypassPermissions "$PROMPT" > "$OUT" 2>&1 9>&-
code=$?
cat "$OUT" >> "$LOG"
if [[ $code -eq 142 ]]; then
  log "exit timeout"
else
  # Only a failed run can be a usage-limit stop; text quoted in a good run must not pause us.
  [[ $DRY -eq 0 && $code -ne 0 ]] && "$PY" -m factory.preflight limit "$OUT" >> "$LOG" 2>&1
  log "exit $code"
fi
exit 0
