"""Merge-time protected-path re-check: `python -m factory.protected_check <repo> <pr> <branch>`.

Defence in depth for factory-merge: the snapshot already excluded protected
PRs, but the file list is fetched again right before merging. Exit 0 only when
the full (paginated) list, renames included, touches no protected path and,
for a guide PR (`content/guide-` branch), stays inside the guide trees of the
marketing repo; 1 when it does not; 2 when the answer is unknown (gh error,
empty or truncated list, or a head branch that is not the one given).
"""
from __future__ import annotations

import re
import sys
from typing import Optional

from factory.config import Config, guide_boundary_ok, load_config, protected_hits
from factory.state import MAX_FILES, Runner, _pr_files, gh

_BRANCH_RE = re.compile(r"^[A-Za-z0-9._/-]+$")


def _head_branch(run: Runner, slug: str, pr: int) -> str:
    return run(["pr", "view", str(pr), "--repo", slug, "--json", "headRefName", "--jq", ".headRefName"]).strip()


def check(repo: str, pr: int, branch: str, run: Runner = gh, cfg: Optional[Config] = None) -> tuple[int, list[str]]:
    cfg = cfg or load_config()
    slug = cfg.repos[repo].slug
    if _head_branch(run, slug, pr) != branch:
        return 2, []
    files = _pr_files(run, slug, pr)
    if not files or len(files) >= MAX_FILES:
        return 2, []
    hits = protected_hits(repo, files, cfg)
    if not guide_boundary_ok(repo, branch, files, cfg.guide_locales):
        hits = hits or ["(guide PR outside src/content/guides/ and public/guides/ of marketing)"]
    return (1 if hits else 0), hits


def main(argv: list[str], run: Runner = gh, cfg: Optional[Config] = None) -> int:
    if (len(argv) != 3 or argv[0] not in ("app", "marketing") or not argv[1].isdigit()
            or not _BRANCH_RE.match(argv[2]) or ".." in argv[2]):
        print("usage: python -m factory.protected_check app|marketing <pr> <branch>", file=sys.stderr)
        return 2
    try:
        code, hits = check(argv[0], int(argv[1]), argv[2], run, cfg)
    except Exception as exc:  # noqa: BLE001 - any failure means "do not merge"
        print(f"UNKNOWN {type(exc).__name__}: {exc}")
        return 2
    print("PROTECTED" if code == 1 else ("UNKNOWN head branch mismatch, or empty or truncated file list" if code == 2 else "OK"))
    for h in hits:
        print(f"  {h}")
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
