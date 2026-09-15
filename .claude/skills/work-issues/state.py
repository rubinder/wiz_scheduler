#!/usr/bin/env python3
"""Snapshot GitHub state for the /work-issues driver and pick one action.

Stdlib only. Always exits 0 and always prints JSON: the skill injects this
output as dynamic context, and a non-zero exit would abort the skill.

All durable state lives on GitHub (labels, branches, PRs). This module only
reads it; the workflows write it.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional

AGENT_MARKER = "<!-- wizbot -->"
BRANCH_PREFIX = "agent/"
STALE_HOURS = 3
LABEL_READY = "agent-ready"
LABEL_WORKING = "agent-working"
LABEL_PR_OPEN = "agent-pr-open"
LABEL_BLOCKED = "agent-blocked"

Runner = Callable[[list[str]], str]

_CLOSES_RE = re.compile(r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+#(\d+)", re.I)
_BRANCH_ISSUE_RE = re.compile(r"^agent/issue-(\d+)(?:-|$)")
_CI_COMMENT_RE = re.compile(r"^failing checks:\s*(.+)$", re.I | re.M)


# --- pure helpers ---------------------------------------------------------------

def _parse_ts(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_stale(item: dict[str, Any], now: datetime) -> bool:
    """A working issue is stale when its branch (or, failing that, the
    issue itself) has been quiet for STALE_HOURS."""
    anchor = _parse_ts(item.get("last_commit_at")) or _parse_ts(item.get("updated_at"))
    if anchor is None:
        return True
    return now - anchor > timedelta(hours=STALE_HOURS)


def checks_from_rollup(rollup: list[dict[str, Any]]) -> str:
    if not rollup:
        return "none"
    states = [(c.get("conclusion") or c.get("state") or c.get("status") or "").upper() for c in rollup]
    if any(s in {"FAILURE", "ERROR", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED"} for s in states):
        return "failure"
    if any(s in {"", "PENDING", "IN_PROGRESS", "QUEUED", "WAITING", "EXPECTED"} for s in states):
        return "pending"
    return "success"


def failing_check_names(rollup: list[dict[str, Any]]) -> list[str]:
    names = []
    for c in rollup:
        s = (c.get("conclusion") or c.get("state") or "").upper()
        if s in {"FAILURE", "ERROR", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED"}:
            names.append(c.get("name") or c.get("context") or "unknown")
    return sorted(set(names))


def issue_for_pr(body: str, branch: str) -> Optional[int]:
    m = _CLOSES_RE.search(body or "")
    if m:
        return int(m.group(1))
    m = _BRANCH_ISSUE_RE.match(branch or "")
    return int(m.group(1)) if m else None


def count_unanswered(threads: list[dict[str, Any]]) -> int:
    """Unresolved threads whose latest comment is not the agent's."""
    n = 0
    for t in threads:
        if t.get("isResolved"):
            continue
        comments = (t.get("comments") or {}).get("nodes") or []
        last = comments[-1].get("body", "") if comments else ""
        if AGENT_MARKER not in last:
            n += 1
    return n


def last_ci_comment_checks(comments: list[dict[str, Any]]) -> list[str]:
    """Check names named in the agent's most recent CI-fix comment."""
    for c in reversed(comments):
        body = c.get("body") or ""
        if AGENT_MARKER not in body:
            continue
        m = _CI_COMMENT_RE.search(body)
        if m:
            return sorted({x.strip() for x in m.group(1).split(",") if x.strip()})
    return []


def choose_action(snapshot: dict[str, Any], now: datetime) -> dict[str, Any]:
    prs = sorted(snapshot.get("prs") or [], key=lambda p: p["number"])
    issues = sorted(snapshot.get("issues") or [], key=lambda i: i["number"])

    live_prs = [p for p in prs if LABEL_BLOCKED not in (p.get("labels") or [])]

    # 1. Red CI on an agent PR.
    for p in live_prs:
        if p.get("checks") == "failure":
            failing = list(p.get("failing_checks") or [])
            repeat = bool(failing) and failing == list(p.get("last_ci_comment_checks") or [])
            return {
                "kind": "pr-tend", "workflow": "pr-tend",
                "args": {"pr": p["number"], "mode": "ci", "repeat_failure": repeat},
                "reason": f"PR #{p['number']} has failing checks: {', '.join(failing) or 'unknown'}",
            }

    # 2. Human review needs a response.
    for p in live_prs:
        if (p.get("unanswered_threads") or 0) > 0:
            return {
                "kind": "pr-tend", "workflow": "pr-tend",
                "args": {"pr": p["number"], "mode": "review"},
                "reason": f"PR #{p['number']} has {p['unanswered_threads']} unanswered review thread(s)",
            }
        if p.get("review_decision") == "CHANGES_REQUESTED":
            reviewed = _parse_ts(p.get("last_review_at"))
            pushed = _parse_ts(p.get("last_agent_push_at"))
            if reviewed and (pushed is None or reviewed > pushed):
                return {
                    "kind": "pr-tend", "workflow": "pr-tend",
                    "args": {"pr": p["number"], "mode": "review"},
                    "reason": f"PR #{p['number']} has changes requested since the last push",
                }

    # 3. Next opted-in issue.
    for i in issues:
        labels = set(i.get("labels") or [])
        if LABEL_READY in labels and not labels & {LABEL_WORKING, LABEL_PR_OPEN, LABEL_BLOCKED}:
            return {
                "kind": "issue-pipeline", "workflow": "issue-pipeline",
                "args": {"issue": i["number"]},
                "reason": f"issue #{i['number']} is agent-ready",
            }

    # 4. Resume a run that died.
    for i in issues:
        labels = set(i.get("labels") or [])
        if LABEL_WORKING in labels and LABEL_BLOCKED not in labels and _is_stale(i, now):
            return {
                "kind": "issue-pipeline", "workflow": "issue-pipeline",
                "args": {"issue": i["number"]},
                "reason": f"issue #{i['number']} is agent-working but stale (> {STALE_HOURS}h quiet); resuming",
            }

    return {"kind": "idle", "reason": "no agent-ready issues and no agent PRs need attention"}


def list_stale_worktrees(snapshot: dict[str, Any], worktrees_dir: Path) -> list[Path]:
    """issue-N worktrees whose issue has neither an open agent PR nor a
    working label. Safe to remove; the driver does it before choosing."""
    keep = {p.get("issue") for p in snapshot.get("prs") or []}
    keep |= {i["number"] for i in snapshot.get("issues") or []
             if LABEL_WORKING in (i.get("labels") or [])}
    stale = []
    if not worktrees_dir.is_dir():
        return stale
    for path in sorted(worktrees_dir.iterdir()):
        m = re.fullmatch(r"issue-(\d+)", path.name)
        if m and int(m.group(1)) not in keep:
            stale.append(path)
    return stale


# --- collectors -----------------------------------------------------------------

_PR_GRAPHQL = """
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      reviewThreads(first: 100) { nodes { isResolved comments(last: 1) { nodes { body createdAt } } } }
      reviews(last: 20) { nodes { state submittedAt } }
      comments(last: 50) { nodes { body } }
    }
  }
}
"""


def gh(args: list[str]) -> str:
    proc = subprocess.run(["gh", *args], capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args[:3])} failed: {proc.stderr.strip()[:400]}")
    return proc.stdout


def _repo(run: Runner) -> tuple[str, str]:
    out = json.loads(run(["repo", "view", "--json", "nameWithOwner"]))
    owner, name = out["nameWithOwner"].split("/", 1)
    return owner, name


def _branch_last_commit(run: Runner, owner: str, name: str, branch: str) -> Optional[str]:
    try:
        out = json.loads(run(["api", f"repos/{owner}/{name}/branches/{branch}"]))
    except Exception:
        return None
    return (((out.get("commit") or {}).get("commit") or {}).get("committer") or {}).get("date")


def _issues(run: Runner, owner: str, name: str) -> list[dict[str, Any]]:
    seen: dict[int, dict[str, Any]] = {}
    for label in (LABEL_READY, LABEL_WORKING, LABEL_BLOCKED):
        rows = json.loads(run(["issue", "list", "--state", "open", "--label", label,
                               "--json", "number,title,labels,updatedAt", "--limit", "100"]))
        for r in rows:
            seen[r["number"]] = {
                "number": r["number"], "title": r.get("title", ""),
                "labels": sorted(l["name"] for l in r.get("labels") or []),
                "updated_at": r.get("updatedAt"), "last_commit_at": None,
            }
    for item in seen.values():
        if LABEL_WORKING in item["labels"]:
            # Branch slug is unknown here; the pipeline names branches agent/issue-N-<slug>.
            # Ask the API for matching refs.
            try:
                refs = json.loads(run(["api", f"repos/{owner}/{name}/git/matching-refs/heads/agent/issue-{item['number']}-"]))
            except Exception:
                refs = []
            if refs:
                branch = refs[0]["ref"].removeprefix("refs/heads/")
                item["last_commit_at"] = _branch_last_commit(run, owner, name, branch)
    return sorted(seen.values(), key=lambda i: i["number"])


def _prs(run: Runner, owner: str, name: str) -> list[dict[str, Any]]:
    rows = json.loads(run(["pr", "list", "--state", "open", "--search", f"head:{BRANCH_PREFIX}",
                           "--json", "number,headRefName,labels,reviewDecision,statusCheckRollup,body",
                           "--limit", "20"]))
    prs = []
    for r in rows:
        branch = r.get("headRefName") or ""
        if not branch.startswith(BRANCH_PREFIX):
            continue
        rollup = r.get("statusCheckRollup") or []
        last_commit = _branch_last_commit(run, owner, name, branch)
        detail = json.loads(run(["api", "graphql", "-f", f"query={_PR_GRAPHQL}",
                                 "-F", f"owner={owner}", "-F", f"name={name}", "-F", f"number={r['number']}"]))
        node = ((detail.get("data") or {}).get("repository") or {}).get("pullRequest") or {}
        threads = (node.get("reviewThreads") or {}).get("nodes") or []
        reviews = (node.get("reviews") or {}).get("nodes") or []
        comments = (node.get("comments") or {}).get("nodes") or []
        review_times = [rv.get("submittedAt") for rv in reviews if rv.get("state") == "CHANGES_REQUESTED"]
        prs.append({
            "number": r["number"], "branch": branch,
            "issue": issue_for_pr(r.get("body") or "", branch),
            "checks": checks_from_rollup(rollup),
            "failing_checks": failing_check_names(rollup),
            "review_decision": r.get("reviewDecision") or "",
            "unanswered_threads": count_unanswered(threads),
            "labels": sorted(l["name"] for l in r.get("labels") or []),
            "last_agent_push_at": last_commit,
            "last_commit_at": last_commit,
            "last_review_at": max(review_times) if review_times else None,
            "last_ci_comment_checks": last_ci_comment_checks(comments),
        })
    return sorted(prs, key=lambda p: p["number"])


def collect(run: Runner, now: datetime) -> dict[str, Any]:
    owner, name = _repo(run)
    snapshot: dict[str, Any] = {
        "generated_at": _iso(now),
        "repo": f"{owner}/{name}",
        "issues": _issues(run, owner, name),
        "prs": _prs(run, owner, name),
    }
    snapshot["action"] = choose_action(snapshot, now)
    return snapshot


def main(argv: list[str], run: Runner = gh, now: Optional[datetime] = None) -> int:
    now = now or datetime.now(timezone.utc)
    try:
        snapshot = collect(run, now)
        worktrees = Path(__file__).resolve().parents[3] / ".claude" / "worktrees"
        snapshot["stale_worktrees"] = [str(p) for p in list_stale_worktrees(snapshot, worktrees)]
    except Exception as exc:  # noqa: BLE001 - the skill must always get JSON
        snapshot = {
            "generated_at": _iso(now), "error": f"{type(exc).__name__}: {exc}",
            "issues": [], "prs": [], "stale_worktrees": [],
            "action": {"kind": "idle", "reason": f"snapshot failed: {exc}"},
        }
    print(json.dumps(snapshot, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
