# factory/preflight.py
"""Should this tick run at all? And did the last run hit the usage limit?"""
from __future__ import annotations

import fcntl
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from factory import statefile
from factory.config import FACTORY_HOME, Config, load_config

LOCK = FACTORY_HOME / "tick.lock"
# The UTC date the owner was last told preflight is failing (corrupt state.json,
# bad config). A plain file, so telling them never needs the broken state.json.
ERROR_STAMP = FACTORY_HOME / "preflight-error-notified"
_NOTIFY_SCRIPT = ["-e", "on run argv", "-e", "display notification (item 2 of argv) with title (item 1 of argv)",
                  "-e", "end run"]
_LIMIT_RE = re.compile(r"(usage limit|hit your limit|rate limit reached)", re.I)
_EPOCH_RE = re.compile(r"limit reached\|(\d{9,})")
_CLOCK_RE = re.compile(r"resets\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", re.I)


@dataclass
class Probes:
    on_battery: Callable[[], bool]
    docker_up: Callable[[], bool]
    lock_held: Callable[[], bool]
    stop_file: Callable[[], bool]
    latest_owner_activity: Callable[[], float | None]


def _parse_ts(v: str | None) -> datetime | None:
    """Aware UTC datetime, None if unset. Raises ValueError if unreadable or naive."""
    if not v:
        return None
    if not isinstance(v, str):
        raise ValueError("not a string")
    dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("naive timestamp")
    return dt.astimezone(timezone.utc)


def reasons_to_skip(now: datetime, cfg: Config, local: dict, probes: Probes) -> list[str]:
    reasons: list[str] = []
    if probes.stop_file():
        reasons.append("STOP file present")
    try:
        paused = _parse_ts(local.get("paused_until"))
    except (ValueError, TypeError, OverflowError):
        reasons.append("paused_until unreadable")  # fail closed
    else:
        if paused and paused > now.astimezone(timezone.utc):
            reasons.append(f"paused until {local['paused_until']}")
    if cfg.quiet_hours is not None:
        qh = cfg.quiet_hours
        if (not isinstance(qh, (tuple, list)) or len(qh) != 2
                or not all(isinstance(x, int) and not isinstance(x, bool) and 0 <= x <= 24 for x in qh)):
            reasons.append("quiet_hours malformed")
        else:
            start, end = qh
            h = now.astimezone().hour
            if (start <= h < end) if start < end else (h >= start or h < end):
                reasons.append("quiet hours")
    if probes.on_battery():
        reasons.append("on battery")
    if not probes.docker_up():
        reasons.append("docker not running")
    if probes.lock_held():
        reasons.append("another tick holds the lock")
    last = probes.latest_owner_activity()
    if last is not None and now.timestamp() - last < cfg.idle_minutes * 60:
        reasons.append("owner active")
    return reasons


MAX_PAUSE = timedelta(hours=24)


def parse_limit(output: str, now: datetime) -> datetime | None:
    """Clamped to now + 24 h: the text may quote untrusted content."""
    got = _parse_limit(output, now)
    return None if got is None else min(got, now + MAX_PAUSE)


def _parse_limit(output: str, now: datetime) -> datetime | None:
    """Reset time from a usage-limit message. Never raises: anything it cannot
    parse as a time becomes now + 1 h; non-limit text is None."""
    if not _LIMIT_RE.search(output):
        return None
    fallback = now + timedelta(hours=1)
    try:
        m = _EPOCH_RE.search(output)
        if m:
            return datetime.fromtimestamp(int(m.group(1)), tz=timezone.utc)
        m = _CLOCK_RE.search(output)
        if m:
            hour_12, minute = int(m.group(1)), int(m.group(2) or 0)
            if not (1 <= hour_12 <= 12 and 0 <= minute <= 59):
                return fallback
            hour = hour_12 % 12 + (12 if m.group(3).lower() == "pm" else 0)
            tz = timezone.utc if "(UTC)" in output else now.astimezone().tzinfo
            local_now = now.astimezone(tz)
            reset = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if reset <= local_now:
                reset += timedelta(days=1)
            return reset.astimezone(timezone.utc)
    except (ValueError, OverflowError, OSError):
        return fallback
    return fallback


# Only these lines are someone talking to Claude. An idle open session keeps
# appending bookkeeping lines (cost-state, last-prompt), which move the file's
# mtime but carry no timestamp, so mtime alone reads an idle session as active.
_MESSAGE_TYPES = ("user", "assistant")
_TAIL_START = 64 * 1024


def last_message_at(f: Path) -> float | None:
    """Epoch seconds of the newest user/assistant line in a transcript, reading
    backwards from the end in growing chunks. None if there is none."""
    try:
        size = f.stat().st_size
        with f.open("rb") as fh:
            n = _TAIL_START
            while True:
                fh.seek(max(0, size - n))
                lines = fh.read(min(n, size)).splitlines()
                if n < size:
                    lines = lines[1:]  # the first line is probably cut off
                for raw in reversed(lines):
                    got = _message_ts(raw)
                    if got is not None:
                        return got
                if n >= size:
                    return None
                n *= 4
    except OSError:
        return None  # vanished or unreadable mid-scan


def _message_ts(raw: bytes) -> float | None:
    if b'"timestamp"' not in raw:
        return None
    try:
        d = json.loads(raw)
        if d.get("type") not in _MESSAGE_TYPES:
            return None
        return _parse_ts(d.get("timestamp")).timestamp()
    except (ValueError, TypeError, AttributeError, OverflowError):
        return None


def latest_owner_activity(projects: Path, factory_home: Path = FACTORY_HOME) -> float | None:
    """When the owner last exchanged a message with Claude, as epoch seconds,
    across transcripts outside the factory's own clones. Claude Code names
    project dirs by the cwd with '/' and '_' replaced by '-'."""
    prefix = re.sub(r"[/_.]", "-", str(factory_home))
    if not projects.is_dir():
        return None
    files: list[tuple[float, Path]] = []
    for d in projects.iterdir():
        if d.name.startswith(prefix):
            continue
        for f in d.glob("*.jsonl"):
            try:
                files.append((f.stat().st_mtime, f))
            except FileNotFoundError:
                continue  # vanished mid-scan
    newest: float | None = None
    for mtime, f in sorted(files, key=lambda x: x[0], reverse=True):
        if newest is not None and mtime < newest:
            break  # a file cannot hold a message newer than its last write
        got = last_message_at(f)
        if got is not None and (newest is None or got > newest):
            newest = got
    return newest


def real_on_battery() -> bool:
    return _real_probes().on_battery()


def real_docker_up() -> bool:
    return _real_probes().docker_up()


def _real_probes() -> Probes:
    def on_battery() -> bool:
        try:
            out = subprocess.run(["pmset", "-g", "batt"], capture_output=True, text=True, timeout=10).stdout
        except subprocess.TimeoutExpired:
            return True  # cannot tell: treat as on battery
        return "Battery Power" in out

    def docker_up() -> bool:
        try:
            return subprocess.run(["docker", "info"], capture_output=True, timeout=20).returncode == 0
        except subprocess.TimeoutExpired:
            return False

    def lock_held() -> bool:
        if os.environ.get("FACTORY_TICK_HOLDS_LOCK") == "1":
            return False  # tick.sh took the lock before calling preflight
        LOCK.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(LOCK, os.O_RDWR | os.O_CREAT)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fd, fcntl.LOCK_UN)
            return False
        except BlockingIOError:
            return True
        finally:
            os.close(fd)

    return Probes(on_battery=on_battery, docker_up=docker_up, lock_held=lock_held,
                  stop_file=lambda: (FACTORY_HOME / "STOP").exists(),
                  latest_owner_activity=lambda: latest_owner_activity(Path.home() / ".claude" / "projects"))


def _osascript(argv: list[str]) -> int:
    return subprocess.run(argv, check=False, timeout=30).returncode


def notify_error_once(now: datetime, detail: str) -> bool:
    """Tell the owner at most once per UTC day that every tick is being skipped.

    Never reads or writes state.json (it may be the thing that is broken) and
    never raises: the tick must still skip.
    """
    today = now.astimezone(timezone.utc).strftime("%Y-%m-%d")
    try:
        if ERROR_STAMP.read_text().strip() == today:
            return False
    except OSError:
        pass
    try:
        _osascript(["osascript", *_NOTIFY_SCRIPT, "Factory preflight error",
                    f"Every tick is skipping: preflight error {detail}. Check ~/factory/state.json and config.toml."])
    except Exception:  # noqa: BLE001 - notifying is best effort
        pass
    try:
        ERROR_STAMP.parent.mkdir(parents=True, exist_ok=True)
        ERROR_STAMP.write_text(today + "\n")
    except OSError:
        pass
    return True


def main(argv: list[str]) -> int:
    if not argv:
        return 2
    now = datetime.now(timezone.utc)
    if argv[0] == "check":
        try:
            reasons = reasons_to_skip(now, load_config(), statefile.load(), _real_probes())
        except Exception as e:  # fail closed: an error is never "run"
            print(f"skip: preflight error {type(e).__name__}")
            notify_error_once(now, type(e).__name__)
            return 3
        print("; ".join(reasons) if reasons else "run")
        return 3 if reasons else 0
    if argv[0] == "limit":
        until = parse_limit(Path(argv[1]).read_text(errors="replace"), now)
        if until:
            iso = until.strftime("%Y-%m-%dT%H:%M:%SZ")
            statefile.main(["pause", iso])
            print(f"paused until {iso}")
            if until - now > timedelta(hours=6):
                from factory.notify import notify
                notify("Factory paused", f"Usage limit; paused until {iso}", f"pause-{iso}")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
