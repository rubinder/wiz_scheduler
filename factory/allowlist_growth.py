# factory/allowlist_growth.py
"""Fail when an architecture allowlist has entries that the base version lacks.

Usage: python -m factory.allowlist_growth --worktree DIR --base REF
Exit 0: no new entries. Exit 1: new entries listed. Exit 2: error.
Entries compare by (rule, file, detail), so reordering or shrinking passes.
A list that does not exist at the base counts as empty.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ALLOWLISTS = ("tests/architecture_allowlist.json", "frontend/src/architecture_allowlist.json")
Key = tuple[str, str, str]


class AllowlistError(Exception):
    pass


def _keys(raw: str, where: str) -> set[Key]:
    try:
        return {(e["rule"], e["file"], e["detail"]) for e in json.loads(raw)}
    except (ValueError, KeyError, TypeError) as exc:
        raise AllowlistError(f"{where}: unreadable allowlist ({exc})") from exc


def _git(worktree: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(worktree), *args], capture_output=True, text=True)


def new_entries(worktree: Path, base: str) -> list[tuple[str, Key]]:
    if _git(worktree, "rev-parse", "--verify", f"{base}^{{commit}}").returncode != 0:
        raise AllowlistError(f"unknown base ref: {base}")
    found: list[tuple[str, Key]] = []
    for rel in ALLOWLISTS:
        path = worktree / rel
        current = _keys(path.read_text(), rel) if path.exists() else set()
        shown = _git(worktree, "show", f"{base}:{rel}")
        previous = _keys(shown.stdout, f"{base}:{rel}") if shown.returncode == 0 else set()
        found.extend((rel, k) for k in sorted(current - previous))
    return found


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="factory.allowlist_growth")
    ap.add_argument("--worktree", required=True, type=Path)
    ap.add_argument("--base", required=True)
    args = ap.parse_args(argv)
    try:
        added = new_entries(args.worktree, args.base)
    except (AllowlistError, OSError, UnicodeDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    for rel, (rule, file, detail) in added:
        print(f"new allowlist entry in {rel}: {rule} {file} {detail}")
    return 1 if added else 0


if __name__ == "__main__":
    sys.exit(main())
