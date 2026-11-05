# factory/statefile.py
"""~/factory/state.json: the factory's only local state.

Everything else durable lives in git and GitHub labels. Workflows update this
file through the CLI below so the JS never writes JSON by hand.
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from factory.config import FACTORY_HOME


def _path(path: Path | None) -> Path:
    return path or Path(os.environ.get("FACTORY_STATE", str(FACTORY_HOME / "state.json")))


def defaults() -> dict[str, Any]:
    return {"paused_until": None, "pending_verify": [], "guide": {}, "proposals": {}, "findings": [],
            "retros_done": [], "notified": [], "digest_sent": None, "checkpointed": [], "events": [], "gate_passes": [],
            "attempts": [], "main_red": {}, "retried": [], "outbox": [], "outbox_sending": 0}


def load(path: Path | None = None) -> dict[str, Any]:
    p = _path(path)
    state = defaults()
    if p.exists():
        state.update(json.loads(p.read_text()))
    return state


def save(state: dict[str, Any], path: Path | None = None) -> None:
    p = _path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True))
    os.replace(tmp, p)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def event(state: dict[str, Any], kind: str, **data: Any) -> None:
    state["events"] = (state.get("events") or [])[-499:] + [{"at": _now(), "kind": kind, **data}]


def has_gate_pass(state: dict[str, Any], repo: str, pr: int, sha: str) -> bool:
    """True when the factory itself recorded passing gates for this exact head.

    The body marker on a PR is self-asserted text; this local record is the
    factory's own word that the gates ran and passed on that commit.
    """
    sha = (sha or "").lower()
    return bool(sha) and any(g.get("repo") == repo and g.get("pr") == pr and g.get("sha") == sha
                             for g in state.get("gate_passes") or [])


_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_STAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
ATTEMPT_KINDS = ("merge", "fix", "regate")
# After this many tries at the same (kind, repo, pr, head sha) the factory skips
# the PR and tells the owner, instead of re-running the same refusal every tick.
MAX_ATTEMPTS = 3
# A merge whose deploy/smoke result is still unknown after this long is dropped
# from pending_verify and handed to the owner.
VERIFY_TTL = timedelta(hours=24)


def attempts(state: dict[str, Any], kind: str, repo: str, pr: int, sha: str) -> int:
    sha = (sha or "").lower()
    return next((int(a.get("n") or 0) for a in state.get("attempts") or []
                 if (a.get("kind"), a.get("repo"), a.get("pr"), a.get("sha")) == (kind, repo, pr, sha)), 0)


def verify_expired(entry: dict[str, Any], now: datetime) -> bool:
    """True for a pending verify older than VERIFY_TTL, or with no readable time (fail closed)."""
    try:
        at = datetime.fromisoformat(str(entry.get("at") or "").replace("Z", "+00:00"))
    except ValueError:
        return True
    if at.tzinfo is None:
        return True
    return now - at > VERIFY_TTL


def main_red_finding(repo: str, date: str) -> dict[str, Any]:
    """The public finding Propose files when main CI is red: driver-built text only."""
    return {"source": "main-ci-red", "public": True, "repo": repo, "key": f"main-ci-red-{repo}-{date}",
            "title": f"main CI is red in the {repo} repo ({date})",
            "body": "The latest CI run on main failed. The factory merges nothing into this repo until main is green again."}


def _gate_pass_entry(args: list[str]) -> dict[str, Any] | None:
    """The validated gate-pass record, or None when any input is malformed."""
    if len(args) != 3:
        return None
    repo, pr, sha = args[0], args[1], args[2].lower()
    if repo not in ("app", "marketing") or not pr.isascii() or not pr.isdigit() or int(pr) < 1 or not _SHA_RE.fullmatch(sha):
        return None
    return {"repo": repo, "pr": int(pr), "sha": sha}


def _attempt_key(args: list[str]) -> tuple[str, str, int, str] | None:
    if len(args) != 4 or args[0] not in ATTEMPT_KINDS:
        return None
    entry = _gate_pass_entry(args[1:])
    return None if entry is None else (args[0], entry["repo"], entry["pr"], entry["sha"])


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: python -m factory.statefile <command> ...", file=sys.stderr)
        return 2
    s = load()
    cmd, rest = argv[0], argv[1:]
    if cmd == "has-gate-pass":
        return 0 if has_gate_pass(s, rest[0], int(rest[1]), rest[2]) else 1
    if cmd == "gate-pass":
        entry = _gate_pass_entry(rest)
        if entry is None:
            print("gate-pass needs: <app|marketing> <positive pr number> <40-hex sha>", file=sys.stderr)
            return 2
        s["gate_passes"] = (s.get("gate_passes") or [])[-499:] + [entry]
        event(s, "gate_pass", repo=entry["repo"], pr=entry["pr"])
    elif cmd == "attempt":
        key = _attempt_key(rest)
        if key is None:
            print(f"attempt needs: <{'|'.join(ATTEMPT_KINDS)}> <app|marketing> <positive pr number> <40-hex sha>",
                  file=sys.stderr)
            return 2
        kind, repo, pr, sha = key
        n = attempts(s, kind, repo, pr, sha) + 1
        others = [a for a in s.get("attempts") or [] if (a.get("kind"), a.get("repo"), a.get("pr"), a.get("sha")) != key]
        s["attempts"] = others[-499:] + [{"kind": kind, "repo": repo, "pr": pr, "sha": sha, "n": n}]
        event(s, "attempt", attempt=kind, repo=repo, pr=pr, n=n)
        print(n)
    elif cmd == "retried":
        if (len(rest) != 3 or rest[0] not in ("app", "marketing") or not rest[1].isdigit() or int(rest[1]) < 1
                or not _STAMP_RE.match(rest[2])):
            print("retried needs: <app|marketing> <positive issue number> <UTC timestamp like 2026-11-05T12:00:00Z>",
                  file=sys.stderr)
            return 2
        s["retried"] = (s.get("retried") or [])[-199:] + [f"{rest[0]}-{int(rest[1])}-{rest[2]}"]
        event(s, "retry", repo=rest[0], issue=int(rest[1]))
    elif cmd == "main-red":
        if len(rest) != 1 or rest[0] not in ("app", "marketing"):
            print("main-red needs: <app|marketing>", file=sys.stderr)
            return 2
        today = _now()[:10]
        seen = s.get("main_red") or {}
        if seen.get(rest[0]) == today:
            print("seen")
            return 0
        s["main_red"] = {**seen, rest[0]: today}
        s["findings"] = (s.get("findings") or []) + [main_red_finding(rest[0], today)]
        event(s, "main_red", repo=rest[0])
        print("new")
    elif cmd == "add-verify":
        s["pending_verify"].append({"repo": rest[0], "pr": int(rest[1]), "sha": rest[2], "result": None, "at": _now()})
        event(s, "merged", repo=rest[0], pr=int(rest[1]))
    elif cmd == "verified":
        repo, pr, result = rest[0], int(rest[1]), rest[2]
        s["pending_verify"] = [v for v in s["pending_verify"] if not (v["repo"] == repo and v["pr"] == pr)]
        kind = {"pass": "smoke_pass", "expired": "verify_expired"}.get(result, "smoke_fail")
        event(s, kind, repo=repo, pr=pr)
    elif cmd == "pause":
        s["paused_until"] = rest[0]
        event(s, "pause", until=rest[0])
    elif cmd == "guide":
        s["guide"] = {"date": rest[0], "status": rest[1]}
        event(s, f"guide_{rest[1]}", date=rest[0])
    elif cmd == "proposal":
        today = _now()[:10]
        p = s.get("proposals") or {}
        s["proposals"] = {"date": today, "count": (p.get("count", 0) if p.get("date") == today else 0) + 1}
        event(s, "proposal")
    elif cmd == "retro-done":
        s["retros_done"] = sorted(set(s["retros_done"]) | {rest[0]})
        event(s, "retro", key=rest[0])
    elif cmd == "checkpoint":
        s["checkpointed"].append({"repo": rest[0], "issue": int(rest[1])})
    elif cmd == "clear-checkpoint":
        s["checkpointed"] = [c for c in s["checkpointed"] if not (c["repo"] == rest[0] and c["issue"] == int(rest[1]))]
    elif cmd == "finding":
        s["findings"].append(json.loads(rest[0]))
    elif cmd == "pop-findings":
        n = int(rest[0])
        print(json.dumps(s["findings"][:n]))
        s["findings"] = s["findings"][n:]
    elif cmd == "event":
        event(s, rest[0], **json.loads(rest[1]) if len(rest) > 1 else {})
    else:
        print(f"unknown command {cmd}", file=sys.stderr)
        return 2
    save(s)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
