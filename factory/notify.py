# factory/notify.py
"""Mac notifications, also queued for email.

Text goes in as osascript argv, never into the script. Every notification sent
is also queued in state.json's outbox; the tick session emails the queue to the
owner (factory/outbox.py) so alerts reach their phone.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Callable

from factory import statefile

OUTBOX_MAX = 50
_SCRIPT = ["-e", "on run argv", "-e", "display notification (item 2 of argv) with title (item 1 of argv)", "-e", "end run"]


def _osascript(argv: list[str]) -> int:
    return subprocess.run(argv, check=False).returncode


def notify(title: str, message: str, key: str | None, run: Callable[[list[str]], int] = _osascript,
           state_path: Path | None = None) -> bool:
    state = statefile.load(state_path)
    if key and key in state["notified"]:
        return False
    run(["osascript", *_SCRIPT, title, message[:240]])
    state["outbox"] = (state.get("outbox") or [])[-(OUTBOX_MAX - 1):] + [
        {"at": statefile._now(), "title": title, "message": message[:2000], "key": key}]
    if key:
        state["notified"] = (state["notified"] + [key])[-500:]
    statefile.event(state, "notify", title=title, key=key)
    statefile.save(state, state_path)
    return True


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--title", required=True)
    ap.add_argument("--message", required=True)
    ap.add_argument("--key")
    a = ap.parse_args(argv)
    notify(a.title, a.message, a.key)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
