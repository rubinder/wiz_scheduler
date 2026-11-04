# factory/digest.py
"""Daily digest: what merged, what waits on the owner, what blocked, misses, pauses, gate counts.

The digest is emailed. It lists numbers and owner-authored titles only; it never
carries security finding details.
"""
from __future__ import annotations

import json
import subprocess
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from factory import statefile
from factory.config import FACTORY_HOME, Config, load_config

Runner = Callable[[list[str]], str]


def _lines(items: list[dict[str, Any]], fmt: Callable[[dict[str, Any]], str]) -> str:
    return "\n".join(f"- {fmt(i)}" for i in items) if items else "- none"


def build(local: dict[str, Any], rows: dict[str, Any], day: str, since: str = "") -> str:
    events = [e for e in local.get("events") or [] if e.get("at", "") >= since]
    pauses = [e for e in events if e["kind"] == "pause"]
    gates: dict[str, Counter[str]] = {}
    for e in events:
        if e["kind"] == "gate":
            gates.setdefault(e["gate"], Counter())[e["result"]] += 1
    gate_lines = [f"- {g}: {c['pass']} pass, {c['fail']} fail" for g, c in sorted(gates.items())] or ["- none"]
    return "\n\n".join([
        f"# Factory digest — {day}",
        "## Merged\n" + _lines(rows["merged"], lambda r: f"{r['repo']}#{r['number']} {r['title']}"),
        "## Waiting on you\n" + _lines(rows["awaiting"], lambda r: f"{r['repo']}#{r['pr']} {r['title']} ({r.get('reason', 'touches a protected path')}: {', '.join(r['protected'])})"),
        "## Blocked\n" + _lines(rows["blocked"], lambda r: f"{r['repo']}#{r['number']} {r['title']}"),
        "## Proposals\n" + _lines(rows["proposals"], lambda r: f"{r['repo']}#{r['number']} {r['title']}"),
        "## Misses\n" + _lines(rows["misses"], lambda r: f"{r['repo']}#{r['number']} {r['kind']}: {r['title']}"),
        "## Usage-limit pauses\n" + _lines(pauses, lambda e: f"{e['at']} until {e['until']}"),
        "## Gates\n" + "\n".join(gate_lines),
    ]) + "\n"


def gather(run: Runner, cfg: Config, since: str) -> dict[str, Any]:
    from factory import state as st
    rows: dict[str, Any] = {"merged": [], "awaiting": [], "blocked": [], "proposals": [], "misses": []}
    for key, repo in cfg.repos.items():
        merged = json.loads(run(["pr", "list", "--repo", repo.slug, "--state", "merged", "--search", f"merged:>={since[:10]}",
                                 "--json", "number,title,headRefName,author", "--limit", "50"]) or "[]")
        rows["merged"] += [{"repo": key, "number": r["number"], "title": r["title"]} for r in merged
                           if r["headRefName"].startswith(st.FACTORY_PREFIXES) and st._is_owner(r, cfg.owner_login)]
        for label, bucket in (("agent-blocked", "blocked"), ("agent-proposed", "proposals")):
            items = json.loads(run(["issue", "list", "--repo", repo.slug, "--state", "open", "--label", label,
                                    "--json", "number,title,author", "--limit", "50"]) or "[]")
            rows[bucket] += [{"repo": key, "number": r["number"], "title": r["title"]} for r in items
                             if st._is_owner(r, cfg.owner_login)]
    snap = st.collect(run, cfg, datetime.now(timezone.utc), statefile.load())
    rows["awaiting"] = st.awaiting_owner(snap, cfg)
    rows["misses"] = snap["misses"]
    return rows


def _gh(argv: list[str]) -> str:
    return subprocess.run(["gh", *argv], capture_output=True, text=True, check=True).stdout


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else ""
    local = statefile.load()
    today = datetime.now().astimezone().strftime("%Y-%m-%d")
    if cmd == "write":
        since = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        md = build(local, gather(_gh, load_config(), since), today, since)
        out = FACTORY_HOME / "digests" / f"{today}.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(md)
        print(out)
    elif cmd == "sent":
        local["digest_sent"] = today
        statefile.save(local)
    else:
        print("usage: digest write|sent", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
