#!/bin/bash
# Local stack for gates: Postgres (Docker), migrations, seed, API and Vite.
# Usage: stack.sh up | down | migrate-roundtrip   (run from a worktree root)
# Only ever talks to a throwaway Postgres on localhost:55432.
set -euo pipefail
umask 077
ROOT="$(pwd)"
PY="${PY:-$ROOT/backend/.venv/bin/python}"
FACTORY_HOME="${FACTORY_HOME:-$HOME/factory}"
NAME=wizfactory-pg
PORT=55432
# backend/config.py reads DATABASE_URL; backend/alembic/env.py uses settings.DATABASE_URL.
# Refuse if the caller's environment already points somewhere non-local, then
# override unconditionally with the throwaway local database.
if [[ -n "${DATABASE_URL:-}" ]]; then
  in_host="${DATABASE_URL#*@}"; in_host="${in_host%%[:/]*}"
  if [[ "$in_host" != "localhost" && "$in_host" != "127.0.0.1" ]]; then
    echo "refusing to run: DATABASE_URL host '$in_host' is not local" >&2; exit 2
  fi
fi
export DATABASE_URL="postgresql+asyncpg://postgres:postgres@localhost:$PORT/wiz"
export ENV=development

LOCK="$FACTORY_HOME/stack.lock"
# Is the lock left by a dead run? Live owner pid -> no. Missing/non-numeric pid -> only once
# the lock dir is over 2 hours old (a fresh lock may still be between mkdir and writing its pid).
lock_is_stale() {
  local pid
  pid="$(cat "$LOCK/pid" 2>/dev/null || true)"
  if [[ "$pid" =~ ^[0-9]+$ ]]; then ! kill -0 "$pid" 2>/dev/null; return; fi
  [[ -n "$(find "$LOCK" -maxdepth 0 -mmin +120 2>/dev/null)" ]]
}

# $1 = pid whose life keeps the lock alive: for `up` the caller (this script exits while the
# stack stays up), for migrate-roundtrip this script.
acquire_lock() {
  mkdir -p "$FACTORY_HOME"
  if ! mkdir "$LOCK" 2>/dev/null; then
    if lock_is_stale && mv "$LOCK" "$LOCK.stale.$$" 2>/dev/null; then
      echo "reclaiming stale $LOCK (owner is dead): tearing down its leftovers" >&2
      teardown   # the old owner is dead, so its container, pid-verified processes and env are ours to remove
      rm -rf "$LOCK.stale.$$"
      mkdir "$LOCK" 2>/dev/null || { echo "lost the race for $LOCK" >&2; exit 3; }
    else
      echo "another stack run holds $LOCK (a lock whose owner is dead is reclaimed automatically)" >&2; exit 3
    fi
  fi
  OWNER="$$-$(openssl rand -hex 8)"
  echo "$OWNER" > "$LOCK/owner"
  echo "$1" > "$LOCK/pid"
}
release_lock() { rm -f "$LOCK/owner" "$LOCK/pid"; rmdir "$LOCK" 2>/dev/null || true; }
# True only for the run that holds the lock: STACK_OWNER (from stack.env) must match.
owner_ok() { [[ -n "${STACK_OWNER:-}" && -f "$LOCK/owner" && "$(cat "$LOCK/owner")" == "$STACK_OWNER" ]]; }
teardown() {
  stop_pid stack-api "uvicorn backend.main"
  stop_pid stack-vite "vite --port 5173"
  rm -f "$FACTORY_HOME/stack.env"
  docker rm -f "$NAME" >/dev/null 2>&1 || true
}

# Kill a pid file's process only if it is still ours; always remove the file.
stop_pid() {  # $1 = name, $2 = command substring
  local f="$FACTORY_HOME/$1.pid" pid cmd
  [[ -f "$f" ]] || return 0
  pid="$(cat "$f" 2>/dev/null || true)"
  if [[ "$pid" =~ ^[0-9]+$ ]]; then
    cmd="$(ps -o command= -p "$pid" 2>/dev/null || true)"
    if [[ "$cmd" == *"$2"* ]]; then pkill -P "$pid" 2>/dev/null || true; kill "$pid" 2>/dev/null || true; fi
  fi
  rm -f "$f"
}

pg_up() {
  docker rm -f "$NAME" >/dev/null 2>&1 || true
  docker run -d --name "$NAME" -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=wiz -p "127.0.0.1:$PORT:5432" postgres:16 >/dev/null
  for _ in $(seq 1 30); do docker exec "$NAME" pg_isready -U postgres >/dev/null 2>&1 && return 0; sleep 1; done
  echo "postgres did not start" >&2; return 1
}

case "${1:-}" in
  up)
    acquire_lock "$PPID"   # exits 3 without touching anything when the lock is held
    trap 'teardown; release_lock' EXIT   # any failure after this point cleans up its own state
    rm -f "$FACTORY_HOME/stack-api.pid" "$FACTORY_HOME/stack-vite.pid"
    pg_up
    (cd backend && "$PY" -m alembic upgrade head)
    export DEMO_SEED_PASSWORD="$(openssl rand -hex 16)"
    "$PY" -m backend.seed
    printf 'export VERIFY_EMAIL=abc@example.com\nexport VERIFY_PASSWORD=%s\nexport STACK_OWNER=%s\n' "$DEMO_SEED_PASSWORD" "$OWNER" > "$FACTORY_HOME/stack.env"
    chmod 600 "$FACTORY_HOME/stack.env"
    "$PY" -m uvicorn backend.main:app --port 8000 > "$FACTORY_HOME/stack-api.log" 2>&1 9>&- & echo $! > "$FACTORY_HOME/stack-api.pid"
    (cd frontend && npx --no-install vite --port 5173 --strictPort > "$FACTORY_HOME/stack-vite.log" 2>&1 9>&- & echo $! > "$FACTORY_HOME/stack-vite.pid")
    for _ in $(seq 1 60); do curl -sf http://localhost:5173 >/dev/null && curl -sf http://localhost:8000/health/live >/dev/null && { healthy=1; break; }; sleep 1; done
    [[ "${healthy:-0}" == 1 ]] || { echo "stack did not become healthy" >&2; exit 1; }
    trap - EXIT   # success: the stack stays up, down tears it down
    ;;
  down)
    if [[ ! -d "$LOCK" ]]; then exit 0; fi
    if ! owner_ok; then echo "STACK_OWNER does not match the lock owner; nothing torn down" >&2; exit 3; fi
    teardown
    release_lock
    ;;
  migrate-roundtrip)
    acquire_lock "$$"
    trap 'docker rm -f "$NAME" >/dev/null 2>&1 || true; release_lock' EXIT
    pg_up
    (cd backend && "$PY" -m alembic upgrade head && "$PY" -m alembic downgrade -1 && "$PY" -m alembic upgrade head)
    ;;
  *) echo "usage: stack.sh up|down|migrate-roundtrip" >&2; exit 2 ;;
esac
