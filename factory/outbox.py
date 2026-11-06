# factory/outbox.py
"""Alerts waiting to be emailed to the owner.

notify.py queues every alert in state.json's outbox. At the end of a tick the
session runs `outbox write`, emails the file with the Gmail tool, then runs
`outbox sent`, which drops exactly the alerts that went out. A failed send
leaves them queued for the next tick.

Each alert links to the issue or PR it is about, so the owner can answer on
GitHub, where the comment is tied to their login. Replies to the email itself
are never read: anyone can send mail that claims to be from the owner.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from factory import statefile
from factory.config import FACTORY_HOME, load_config

# A key like blocked-app-78 or owner-marketing-22-stuck-<sha>; a date such as
# main-red-app-2026-11-05 is not a number.
_KEY_RE = re.compile(r"(?:^|-)(app|marketing)-(\d+)(?=$|-(?!\d{2}-\d{2}))")
_MSG_RE = re.compile(r"\b(app|marketing)\b(?:\s+(?:PR|issue))?\s*#(\d+)")
_SLUG_RE = re.compile(r"\b([A-Za-z0-9-]+/[A-Za-z0-9._-]+)\s+(?:PR|issue)\s+#(\d+)")

FOOTER = ("How to answer: comment on the issue or PR linked above, on github.com or in the GitHub mobile app. "
          "The factory reads your comment the next time it runs, within about 30 minutes. "
          "Replies to this email are not read.")
RULE = "-" * 60


def ref(item: dict[str, Any], slugs: dict[str, str]) -> Optional[tuple[str, int]]:
    """(repo key, number) an alert is about, from its key or else its message; None if neither names one."""
    msg = item.get("message") or ""
    for m in (_KEY_RE.search(item.get("key") or ""), _MSG_RE.search(msg)):
        if m and m.group(1) in slugs:
            return m.group(1), int(m.group(2))
    by_slug = {v: k for k, v in slugs.items()}
    for m in _SLUG_RE.finditer(msg):
        if m.group(1) in by_slug:  # never link a repo the factory does not own
            return by_slug[m.group(1)], int(m.group(2))
    return None


def link(item: dict[str, Any], slugs: dict[str, str]) -> Optional[str]:
    """The GitHub URL an alert is about; None if it names no issue or PR."""
    r = ref(item, slugs)
    return None if r is None else f"https://github.com/{slugs[r[0]]}/issues/{r[1]}"


def _heading(item: dict[str, Any], slugs: dict[str, str]) -> str:
    title = item.get("title") or "Factory alert"
    r = ref(item, slugs)
    return title if r is None else f"{title}: {r[0]} #{r[1]}"


def render(items: list[dict[str, Any]], slugs: dict[str, str]) -> str:
    blocks = []
    for it in items:
        url = link(it, slugs)
        lines = [_heading(it, slugs), "", (it.get("message") or "").strip()]
        if url:
            lines += ["", f"Open on GitHub: {url}"]
        blocks.append("\n".join(lines))
    return f"\n\n{RULE}\n\n".join(blocks + [FOOTER]) + "\n"


def subject(items: list[dict[str, Any]], slugs: Optional[dict[str, str]] = None) -> str:
    first = _heading(items[0], slugs or {})
    return f"WizScheduler factory: {first}" + (f" (+{len(items) - 1} more)" if len(items) > 1 else "")


def main(argv: list[str], state_path: Optional[Path] = None, home: Path = FACTORY_HOME) -> int:
    cmd = argv[0] if argv else ""
    s = statefile.load(state_path)
    items = s.get("outbox") or []
    if cmd == "write":
        if not items:
            print(json.dumps({"path": None}))
            return 0
        slugs = {k: r.slug for k, r in load_config().repos.items()}
        out = home / "alerts" / f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.txt"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(render(items, slugs))
        s["outbox_sending"] = len(items)
        statefile.save(s, state_path)
        print(json.dumps({"path": str(out), "subject": subject(items, slugs)}))
    elif cmd == "sent":
        n = int(s.get("outbox_sending") or 0)
        s["outbox"], s["outbox_sending"] = items[n:], 0
        statefile.event(s, "alerts_emailed", n=n)
        statefile.save(s, state_path)
    else:
        print("usage: outbox write|sent", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
