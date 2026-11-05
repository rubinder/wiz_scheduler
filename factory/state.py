"""Factory decision logic: pick the next action from a GitHub snapshot and local state.

Pure functions; collectors that build the snapshot are added separately.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from factory.config import Config, guide_boundary_ok, guide_outside, load_config, protected_hits
from factory import proposals, statefile

_CLOSES_RE = re.compile(r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+#(\d+)", re.I)
_BRANCH_ISSUE_RE = re.compile(r"^agent/issue-(\d+)(?:-|$)")
_CI_COMMENT_RE = re.compile(r"^failing checks:\s*(.+)$", re.I | re.M)

def _parse_ts(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# Only these count as green; still-running states are pending; every other
# value (FAILURE, STALE, STARTUP_FAILURE, an unknown future one) is a failure,
# so a PR never sits "pending" forever on a conclusion nobody expected.
CHECK_PASSING = {"SUCCESS", "SKIPPED", "NEUTRAL"}
CHECK_PENDING = {"", "PENDING", "IN_PROGRESS", "QUEUED", "WAITING", "EXPECTED", "REQUESTED"}


def _check_state(c: dict[str, Any]) -> str:
    return (c.get("conclusion") or c.get("state") or c.get("status") or "").upper()


def checks_from_rollup(rollup: list[dict[str, Any]]) -> str:
    if not rollup:
        return "none"
    states = [_check_state(c) for c in rollup]
    if any(s not in CHECK_PASSING | CHECK_PENDING for s in states):
        return "failure"
    if any(s in CHECK_PENDING for s in states):
        return "pending"
    return "success"


def failing_check_names(rollup: list[dict[str, Any]]) -> list[str]:
    return sorted({c.get("name") or c.get("context") or "unknown" for c in rollup
                   if _check_state(c) not in CHECK_PASSING | CHECK_PENDING})


def issue_for_pr(body: str, branch: str) -> Optional[int]:
    m = _CLOSES_RE.search(body or "")
    if m:
        return int(m.group(1))
    m = _BRANCH_ISSUE_RE.match(branch or "")
    return int(m.group(1)) if m else None


def _is_owner(node: dict[str, Any], owner: str) -> bool:
    if not owner:
        return False
    return ((node.get("author") or {}).get("login") or "").lower() == owner.lower()


def count_unanswered(threads: list[dict[str, Any]], owner: str) -> int:
    """Unresolved threads whose latest owner-authored comment is not the agent's.

    Comments by anyone else are ignored entirely: a thread with only a
    stranger's comment is not unanswered.
    """
    n = 0
    for t in threads:
        if t.get("isResolved"):
            continue
        comments = [c for c in (t.get("comments") or {}).get("nodes") or [] if _is_owner(c, owner)]
        if comments and AGENT_MARKER not in (comments[-1].get("body") or ""):
            n += 1
    return n


def owner_reply_at(comments: list[dict[str, Any]], owner: str) -> Optional[str]:
    """When the owner last commented on a blocked issue after the factory's latest comment, else None.

    The factory posts as the owner, so its comments are told apart by
    AGENT_MARKER. Only the owner's account counts; a stranger's comment never
    unblocks anything. No factory comment at all (an issue parked without one)
    is None, so old comments cannot unblock it.
    """
    mine = [c for c in comments if _is_owner(c, owner)]
    agent = [i for i, c in enumerate(mine) if AGENT_MARKER in (c.get("body") or "")]
    replies = [c for c in mine[agent[-1] + 1:] if AGENT_MARKER not in (c.get("body") or "")] if agent else []
    return (replies[-1].get("createdAt") or None) if replies else None


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


AGENT_MARKER = "<!-- wizbot -->"
FACTORY_PREFIXES = ("agent/", "content/guide-", "factory/")
STALE_HOURS = 3
LABEL_READY, LABEL_WORKING, LABEL_PR_OPEN, LABEL_BLOCKED = "agent-ready", "agent-working", "agent-pr-open", "agent-blocked"
REPO_ORDER = ("app", "marketing")
MAX_PROPOSALS_PER_DAY = 3
REASON_PROTECTED, REASON_GUIDE = "touches a protected path", "guide outside boundary"
REASON_RETRO = "retro ready for review"
REASON_STALE = "gates stale after fix"
REASON_STUCK = "stuck after repeated attempts"
# Factory PRs whose gates factory-regate can re-run; guide PRs are gated by guides-pipeline.
REGATE_PREFIXES = ("agent/", "factory/revert-")
RETRO_BRANCH_PREFIX = "factory/retro-"
_GATES_RE = re.compile(r"<!--\s*factory-gates:\s*(pass|fail)(?:\s+sha=([0-9a-fA-F]{40}))?\s*-->")


def gate_report(body: str, head_sha: str | None) -> str:
    """"pass"/"fail" only when the marker's sha is the PR's current head.

    A marker with no sha, or one for another commit, is "stale"; no marker is
    "none". Neither ever merges.
    """
    found = _GATES_RE.findall(body or "")
    if not found:
        return "none"
    verdict, sha = found[-1]
    if sha and head_sha and sha.lower() == head_sha.lower():
        return verdict
    return "stale"


def _act(kind: str, repo: str | None, workflow: str | None, args: dict[str, Any], reason: str) -> dict[str, Any]:
    return {"kind": kind, "repo": repo, "workflow": workflow, "args": args, "reason": reason}


def _is_stale(item: dict[str, Any], now: datetime) -> bool:
    anchor = _parse_ts(item.get("last_commit_at")) or _parse_ts(item.get("updated_at"))
    return anchor is None or now - anchor > timedelta(hours=STALE_HOURS)


def had_gate_pass(local: dict[str, Any], repo: str, pr: int) -> bool:
    """True when the factory recorded passing gates for any head of this PR."""
    return any(g.get("repo") == repo and g.get("pr") == pr for g in local.get("gate_passes") or [])


def _exhausted(local: dict[str, Any], kind: str, repo: str, p: dict[str, Any]) -> bool:
    return statefile.attempts(local, kind, repo, p["number"], p.get("head_sha") or "") >= statefile.MAX_ATTEMPTS


def _stale_gates(p: dict[str, Any], repo: str, local: dict[str, Any]) -> bool:
    """Green, and gated before, but not at its current head (a fix was pushed since)."""
    return (p.get("checks") == "success" and not p.get("is_draft")
            and LABEL_BLOCKED not in (p.get("labels") or [])
            and not statefile.has_gate_pass(local, repo, p["number"], p.get("head_sha") or "")
            and had_gate_pass(local, repo, p["number"]))


def _regatable(p: dict[str, Any], repo: str, local: dict[str, Any]) -> bool:
    return ((p.get("branch") or "").startswith(REGATE_PREFIXES) and _stale_gates(p, repo, local)
            and (p.get("unanswered_threads") or 0) == 0 and not _exhausted(local, "regate", repo, p))


def _mergeable(p: dict[str, Any], repo: str, cfg: Config, local: dict[str, Any]) -> bool:
    return (p.get("checks") == "success" and p.get("gate_report") == "pass"
            and statefile.has_gate_pass(local, repo, p["number"], p.get("head_sha") or "")
            and (p.get("unanswered_threads") or 0) == 0
            and p.get("review_decision") != "CHANGES_REQUESTED"
            and not p.get("is_draft")
            and LABEL_BLOCKED not in (p.get("labels") or [])
            and bool(p.get("files"))
            and not p.get("threads_truncated")
            and not p.get("files_truncated")
            and not protected_hits(repo, p.get("files") or [], cfg)
            and guide_boundary_ok(repo, p.get("branch") or "", p.get("files") or [], cfg.guide_locales)
            and not _exhausted(local, "merge", repo, p))


_SHA40_RE = re.compile(r"[0-9a-f]{40}")


def _owner_key(repo: str, p: dict[str, Any], tag: str = "") -> str:
    """The notification key for one awaiting-owner entry.

    notify.py dedupes a key forever, so a condition that can recur on the same
    PR (stuck, stale gates) is keyed by its tag and the head sha it holds at.
    """
    key = f"owner-{repo}-{p['number']}"
    if not tag:
        return key
    sha = str(p.get("head_sha") or "").lower()
    return f"{key}-{tag}-{sha[:12] if _SHA40_RE.fullmatch(sha) else 'nosha'}"


def awaiting_owner(snapshot: dict[str, Any], cfg: Config, local: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Green PRs that only the owner can move on.

    Either they touch a protected path, or they are guide PRs outside the
    guide boundary (`reason` says which; `protected` lists the files).

    Retro PRs (``factory/retro-*``) carry no gate report: they change only the
    factory, the factory never merges them, and they are listed once green.

    A PR whose gates went stale after a fix and that the factory will not
    regate (a guide PR, or regates used up) is "gates stale after fix"; one the
    factory has tried to fix, merge or regate MAX_ATTEMPTS times at its current
    head is "stuck after repeated attempts". A PR the factory is about to
    merge is never listed. Each entry's ``key`` is its notification key.
    """
    local = local if local is not None else statefile.defaults()
    out = []
    for repo in REPO_ORDER:
        for p in snapshot["repos"][repo]["prs"]:
            green = p.get("checks") == "success" and not p.get("is_draft")
            files = p.get("files") or []
            entry = {"repo": repo, "pr": p["number"], "title": p.get("title", ""), "key": _owner_key(repo, p)}
            if (p.get("branch") or "").startswith(RETRO_BRANCH_PREFIX):
                if green:
                    out.append({**entry, "protected": protected_hits(repo, files, cfg), "reason": REASON_RETRO})
                continue
            if _mergeable(p, repo, cfg, local):
                continue
            if any(_exhausted(local, k, repo, p) for k in statefile.ATTEMPT_KINDS):
                out.append({**entry, "key": _owner_key(repo, p, "stuck"),
                            "protected": protected_hits(repo, files, cfg), "reason": REASON_STUCK})
                continue
            if _stale_gates(p, repo, local) and not _regatable(p, repo, local):
                out.append({**entry, "key": _owner_key(repo, p, "stale"),
                            "protected": protected_hits(repo, files, cfg), "reason": REASON_STALE})
                continue
            if not (green and p.get("gate_report") == "pass"):
                continue
            hits = protected_hits(repo, files, cfg)
            if hits:
                out.append({**entry, "protected": hits, "reason": REASON_PROTECTED})
            elif not guide_boundary_ok(repo, p.get("branch") or "", files, cfg.guide_locales):
                out.append({**entry, "protected": guide_outside(repo, files, cfg.guide_locales) or files, "reason": REASON_GUIDE})
    return out


def choose_action(snapshot: dict[str, Any], local: dict[str, Any], now: datetime, cfg: Config) -> dict[str, Any]:
    repos = snapshot["repos"]

    # 1. Fix: red CI or a human waiting for an answer, on any factory PR.
    for repo in REPO_ORDER:
        for p in sorted(repos[repo]["prs"], key=lambda p: p["number"]):
            if LABEL_BLOCKED in (p.get("labels") or []) or _exhausted(local, "fix", repo, p):
                continue
            if p.get("checks") == "failure":
                failing = list(p.get("failing_checks") or [])
                repeat = bool(failing) and failing == list(p.get("last_ci_comment_checks") or [])
                return _act("fix", repo, "pr-tend", {"pr": p["number"], "mode": "ci", "repeat_failure": repeat},
                            f"{repo} PR #{p['number']} has failing checks: {', '.join(failing) or 'unknown'}")
            if (p.get("unanswered_threads") or 0) > 0:
                return _act("fix", repo, "pr-tend", {"pr": p["number"], "mode": "review"},
                            f"{repo} PR #{p['number']} has {p['unanswered_threads']} unanswered review thread(s)")
            if p.get("review_decision") == "CHANGES_REQUESTED":
                reviewed, pushed = _parse_ts(p.get("last_review_at")), _parse_ts(p.get("last_agent_push_at"))
                if reviewed and (pushed is None or reviewed > pushed):
                    return _act("fix", repo, "pr-tend", {"pr": p["number"], "mode": "review"},
                                f"{repo} PR #{p['number']} has changes requested since the last push")

    # 2. Merge: green, gated, unprotected; never into a repo whose main CI is red.
    for repo in REPO_ORDER:
        if repos[repo].get("main_red"):
            continue
        for p in sorted(repos[repo]["prs"], key=lambda p: p["number"]):
            if _mergeable(p, repo, cfg, local):
                return _act("merge", repo, "factory-merge",
                            {"pr": p["number"], "issue": p.get("issue"), "is_revert": bool(p.get("is_revert")),
                             "head_sha": p.get("head_sha"), "branch": p.get("branch")},
                            f"{repo} PR #{p['number']} is green with passing gates")

    # 2b. Regate: a pushed fix left a gated PR without a gate pass at its head.
    for repo in REPO_ORDER:
        for p in sorted(repos[repo]["prs"], key=lambda p: p["number"]):
            if _regatable(p, repo, local):
                return _act("regate", repo, "factory-regate",
                            {"pr": p["number"], "head_sha": p.get("head_sha"), "branch": p.get("branch")},
                            f"{repo} PR #{p['number']} has no gate pass at its new head")

    # 3. Verify: a merge with no smoke result yet (expired ones go to the owner instead).
    for v in local.get("pending_verify") or []:
        if v.get("result") is None and not statefile.verify_expired(v, now):
            return _act("verify", v["repo"], "factory-verify", {"pr": v["pr"], "sha": v["sha"]},
                        f"{v['repo']} PR #{v['pr']} merged without a smoke result")

    # 4. Build: resume a checkpointed or stale run, else the oldest ready issue; one in flight per repo.
    checkpointed = {(c["repo"], c["issue"]) for c in local.get("checkpointed") or []}
    candidates: list[tuple[str, str, dict[str, Any]]] = []
    for repo in REPO_ORDER:
        issues = sorted(repos[repo]["issues"], key=lambda i: i["number"])
        working = [i for i in issues if LABEL_WORKING in i["labels"] and LABEL_BLOCKED not in i["labels"]]
        if working:
            w = working[0]
            if (repo, w["number"]) in checkpointed or _is_stale(w, now):
                return _act("build", repo, "issue-pipeline", {"issue": w["number"]},
                            f"{repo} issue #{w['number']} is agent-working and resumable; resuming")
            continue
        # The owner answering a blocked issue retries it ahead of new work: they are waiting on it.
        # Each reply buys one retry (statefile `retried`), so a run that dies before
        # it can comment again does not loop on the same reply every tick.
        retried = set(local.get("retried") or [])
        replied = [i for i in issues if LABEL_BLOCKED in i["labels"] and i.get("owner_reply_at")
                   and f"{repo}-{i['number']}-{i['owner_reply_at']}" not in retried
                   and not set(i["labels"]) & {LABEL_WORKING, LABEL_PR_OPEN}]
        ready = [i for i in issues if LABEL_READY in i["labels"]
                 and not set(i["labels"]) & {LABEL_WORKING, LABEL_PR_OPEN, LABEL_BLOCKED}]
        if replied:
            candidates.append((0, replied[0].get("created_at") or "", repo, replied[0]))
        elif ready:
            candidates.append((1, ready[0].get("created_at") or "", repo, ready[0]))
    if candidates:
        rank, _, repo, i = min(candidates, key=lambda c: (c[0], c[1], REPO_ORDER.index(c[2])))
        if rank == 0:
            return _act("build", repo, "issue-pipeline", {"issue": i["number"], "reply_at": i["owner_reply_at"]},
                        f"{repo} issue #{i['number']} is blocked and the owner replied; retrying")
        return _act("build", repo, "issue-pipeline", {"issue": i["number"]}, f"{repo} issue #{i['number']} is agent-ready")

    # 5. Guide: once a day when the guides line is live and gum is authorised.
    today = now.strftime("%Y-%m-%d")
    guide = local.get("guide") or {}
    guide_pr_open = any(p["branch"].startswith("content/guide-") for p in repos["marketing"]["prs"])
    if (snapshot.get("guides_enabled") and snapshot.get("gum_ready") and not guide_pr_open
            and not (guide.get("date") == today and guide.get("status") in {"merged", "blocked"})):
        return _act("guide", "marketing", "guides-pipeline", {"date": today}, f"no guide merged or blocked for {today}")

    # 6. Retro: a miss without one.
    done = set(local.get("retros_done") or [])
    for m in snapshot.get("misses") or []:
        key = f"{m['repo']}#{m['number']}"
        if key not in done:
            return _act("retro", m["repo"], "factory-retro", {"number": m["number"], "miss": m["kind"], "key": key},
                        f"{key} is a {m['kind']} miss with no retro")

    # 7. Propose: at most three a day, and not again today once a run filed nothing new.
    filed_today = local.get("proposals") or {}
    used = filed_today.get("count", 0) if filed_today.get("date") == today else 0
    if ((snapshot.get("unfiled_findings") or 0) > 0 and used < MAX_PROPOSALS_PER_DAY
            and filed_today.get("checked") != today):
        return _act("propose", "app", "factory-propose", {"max": MAX_PROPOSALS_PER_DAY - used},
                    f"{snapshot['unfiled_findings']} unfiled finding(s)")

    return _act("idle", None, None, {}, "nothing to fix, merge, verify, build, write or propose")


Runner = Callable[[list[str]], str]

_PR_GRAPHQL = """
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      reviewThreads(first: 100) { pageInfo { hasNextPage } nodes { isResolved comments(last: 20) { pageInfo { hasPreviousPage } nodes { body createdAt author { login } } } } }
      reviews(last: 20) { nodes { state submittedAt author { login } } }
      comments(last: 50) { nodes { body author { login } } }
    }
  }
}
"""


def gh(argv: list[str]) -> str:
    proc = subprocess.run(["gh", *argv], capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"gh {' '.join(argv[:3])} failed: {proc.stderr.strip()[:400]}")
    return proc.stdout


def _labels(row: dict[str, Any]) -> list[str]:
    return sorted(l["name"] for l in row.get("labels") or [])


def _branch_last_commit(run: Runner, slug: str, branch: str) -> Optional[str]:
    try:
        out = json.loads(run(["api", f"repos/{slug}/branches/{branch}"]))
    except Exception:
        return None
    if not isinstance(out, dict):
        return None
    return (((out.get("commit") or {}).get("commit") or {}).get("committer") or {}).get("date")


def _issues(run: Runner, slug: str, owner: str) -> list[dict[str, Any]]:
    seen: dict[int, dict[str, Any]] = {}
    for label in (LABEL_READY, LABEL_WORKING, LABEL_BLOCKED):
        rows = json.loads(run(["issue", "list", "--repo", slug, "--state", "open", "--label", label,
                               "--json", "number,title,labels,updatedAt,createdAt,author", "--limit", "100"]) or "[]")
        for r in rows:
            if not _is_owner(r, owner):
                continue
            seen[r["number"]] = {"number": r["number"], "title": r.get("title", ""), "labels": _labels(r),
                                 "updated_at": r.get("updatedAt"), "created_at": r.get("createdAt"),
                                 "last_commit_at": None}
    for item in seen.values():
        if LABEL_BLOCKED in item["labels"]:
            try:
                view = json.loads(run(["issue", "view", str(item["number"]), "--repo", slug, "--json", "comments"]) or "{}")
            except Exception:
                view = {}
            item["owner_reply_at"] = owner_reply_at((view or {}).get("comments") or [], owner)
        if LABEL_WORKING in item["labels"]:
            try:
                refs = json.loads(run(["api", f"repos/{slug}/git/matching-refs/heads/agent/issue-{item['number']}-"]) or "[]")
            except Exception:
                refs = []
            if refs:
                item["last_commit_at"] = _branch_last_commit(run, slug, refs[0]["ref"].removeprefix("refs/heads/"))
    return sorted(seen.values(), key=lambda i: i["number"])


MAX_FILES = 3000  # GitHub's pulls/files ceiling; reaching it means the list may be incomplete


def threads_window_truncated(node: dict[str, Any]) -> bool:
    """True unless the thread data is provably complete (fail closed).

    A missing pullRequest, missing reviewThreads or pageInfo (GraphQL errors,
    null payloads) all count as truncated, so such a PR never merges.
    """
    rt = node.get("reviewThreads") if node else None
    if not isinstance(rt, dict) or not isinstance(rt.get("pageInfo"), dict):
        return True
    if rt["pageInfo"].get("hasNextPage") is not False:
        return True
    for t in rt.get("nodes") or []:
        pi = (t.get("comments") or {}).get("pageInfo")
        if not isinstance(pi, dict) or pi.get("hasPreviousPage") is not False:
            return True
    return False


def _pr_files(run: Runner, slug: str, number: int) -> list[str]:
    """Every changed file, paginated; --paginate may emit several JSON arrays back to back."""
    out = run(["api", f"repos/{slug}/pulls/{number}/files", "--paginate"]) or "[]"
    dec, i, files = json.JSONDecoder(), 0, []
    out = out.strip()
    while i < len(out):
        page, i = dec.raw_decode(out, i)
        for f in page:
            files.append(f["filename"])
            if f.get("previous_filename"):
                files.append(f["previous_filename"])
        while i < len(out) and out[i].isspace():
            i += 1
    return files


def _prs(run: Runner, slug: str, owner: str) -> list[dict[str, Any]]:
    gh_owner, name = slug.split("/", 1)
    rows = json.loads(run(["pr", "list", "--repo", slug, "--state", "open", "--limit", "30", "--json",
                           "number,headRefName,headRefOid,labels,reviewDecision,statusCheckRollup,body,isDraft,"
                           "title,isCrossRepository,author,baseRefName"]) or "[]")
    prs = []
    for r in rows:
        branch = r.get("headRefName") or ""
        if not branch.startswith(FACTORY_PREFIXES):
            continue
        if r.get("isCrossRepository") or not _is_owner(r, owner) or r.get("baseRefName") != "main":
            continue
        rollup = r.get("statusCheckRollup") or []
        last_commit = _branch_last_commit(run, slug, branch)
        detail = json.loads(run(["api", "graphql", "-f", f"query={_PR_GRAPHQL}", "-F", f"owner={gh_owner}",
                                 "-F", f"name={name}", "-F", f"number={r['number']}"]) or "{}")
        node = ((detail.get("data") or {}).get("repository") or {}).get("pullRequest") or {}
        threads = (node.get("reviewThreads") or {}).get("nodes") or []
        threads_truncated = threads_window_truncated(node)
        files = _pr_files(run, slug, r["number"])
        reviews = [x for x in (node.get("reviews") or {}).get("nodes") or [] if _is_owner(x, owner)]
        comments = [x for x in (node.get("comments") or {}).get("nodes") or [] if _is_owner(x, owner)]
        changes = [rv.get("submittedAt") for rv in reviews if rv.get("state") == "CHANGES_REQUESTED"]
        head_sha = r.get("headRefOid") or ""
        prs.append({
            "number": r["number"], "branch": branch, "title": r.get("title", ""), "head_sha": head_sha,
            "issue": issue_for_pr(r.get("body") or "", branch),
            "checks": checks_from_rollup(rollup), "failing_checks": failing_check_names(rollup),
            "review_decision": r.get("reviewDecision") or "",
            "unanswered_threads": count_unanswered(threads, owner),
            "labels": _labels(r), "last_agent_push_at": last_commit, "last_commit_at": last_commit,
            "last_review_at": max(changes) if changes else None,
            "last_ci_comment_checks": last_ci_comment_checks(comments),
            "files": files, "files_truncated": len(files) >= MAX_FILES,
            "threads_truncated": threads_truncated,
            "gate_report": gate_report(r.get("body") or "", head_sha), "is_draft": bool(r.get("isDraft")),
            "is_revert": branch.startswith("factory/revert-"),
        })
    return sorted(prs, key=lambda p: p["number"])


def _main_red(run: Runner, slug: str) -> bool:
    rows = json.loads(run(["run", "list", "--repo", slug, "--branch", "main", "--limit", "1",
                           "--json", "conclusion,status"]) or "[]")
    return bool(rows) and rows[0].get("status") == "completed" and rows[0].get("conclusion") == "failure"


def _misses(run: Runner, cfg: Config) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for key, repo in cfg.repos.items():
        labelled = json.loads(run(["issue", "list", "--repo", repo.slug, "--state", "all", "--search",
                                   "label:factory-miss", "--json", "number,title,author", "--limit", "30"]) or "[]")
        out += [{"repo": key, "number": r["number"], "kind": "label", "title": r.get("title", "")}
                for r in labelled if _is_owner(r, cfg.owner_login)]
        # factory-merge labels a reverted PR (only the PR) factory-miss.
        reverted = json.loads(run(["pr", "list", "--repo", repo.slug, "--state", "all", "--label", "factory-miss",
                                   "--json", "number,title,author", "--limit", "30"]) or "[]")
        out += [{"repo": key, "number": r["number"], "kind": "revert", "title": r.get("title", "")}
                for r in reverted if _is_owner(r, cfg.owner_login)]
        blocked = json.loads(run(["issue", "list", "--repo", repo.slug, "--state", "open", "--label", LABEL_BLOCKED,
                                  "--json", "number,title,author", "--limit", "30"]) or "[]")
        out += [{"repo": key, "number": r["number"], "kind": "blocked", "title": r.get("title", "")}
                for r in blocked if _is_owner(r, cfg.owner_login)]
    return out


def _guides_enabled(cfg: Config) -> bool:
    conf = cfg.repos["marketing"].path / "src" / "content.config.ts"
    return conf.exists() and "guides" in conf.read_text()


def _gum_ready() -> bool:
    proc = subprocess.run(["gum", "auth", "status"], capture_output=True, text=True, check=False) if _which("gum") else None
    return bool(proc and proc.returncode == 0)


def _which(name: str) -> bool:
    from shutil import which
    return which(name) is not None


def _filed_keys(run: Runner, cfg: Config) -> set[str]:
    """``factory-key:`` lines of the owner's agent-proposed issues, open or closed, in both repos.

    A gh failure raises, so the snapshot fails and the tick idles (fail closed).
    """
    keys: set[str] = set()
    for repo in cfg.repos.values():
        rows = json.loads(run(["issue", "list", "--repo", repo.slug, "--state", "all", "--label", "agent-proposed",
                               "--json", "body,author", "--limit", "500"]) or "[]")
        keys |= proposals.factory_keys(r.get("body") for r in rows if _is_owner(r, cfg.owner_login))
    return keys


def _unfiled_findings(local: dict[str, Any], cfg: Config, filed_keys: set[str]) -> int:
    """Findings waiting in state plus allowlist cleanups not yet filed as an owner issue."""
    n = len(local.get("findings") or [])
    n += sum(1 for c in proposals.from_allowlists(cfg.repos["app"].path) if c["key"] not in filed_keys)
    return n


def collect(run: Runner, cfg: Config, now: datetime, local: dict[str, Any] | None = None) -> dict[str, Any]:
    local = local or statefile.defaults()
    return {
        "generated_at": _iso(now),
        "repos": {key: {"issues": _issues(run, r.slug, cfg.owner_login), "prs": _prs(run, r.slug, cfg.owner_login),
                        "main_red": _main_red(run, r.slug)}
                  for key, r in cfg.repos.items()},
        "misses": _misses(run, cfg),
        "guides_enabled": _guides_enabled(cfg),
        "gum_ready": _gum_ready(),
        "unfiled_findings": _unfiled_findings(local, cfg, _filed_keys(run, cfg)),
    }


def list_stale_worktrees(snapshot: dict[str, Any], cfg: Config) -> list[str]:
    stale: list[str] = []
    for key, repo in cfg.repos.items():
        wt = repo.path / ".claude" / "worktrees"
        if not wt.is_dir():
            continue
        keep = {p.get("issue") for p in snapshot["repos"][key]["prs"]} | {i["number"] for i in snapshot["repos"][key]["issues"]}
        for path in sorted(wt.iterdir()):
            m = re.fullmatch(r"issue-(\d+)", path.name)
            if m and int(m.group(1)) not in keep:
                stale.append(str(path))
    return stale


def digest_due(local: dict[str, Any], cfg: Config, now_local: datetime) -> bool:
    return now_local.hour >= cfg.digest_hour and local.get("digest_sent") != now_local.strftime("%Y-%m-%d")


def main(argv: list[str], run: Runner = gh, now: Optional[datetime] = None, cfg: Optional[Config] = None) -> int:
    now = now or datetime.now(timezone.utc)
    out: dict[str, Any]
    try:
        local = statefile.load()
        cfg = cfg or load_config()
        snapshot = collect(run, cfg, now, local)
        out = {"snapshot": snapshot, "action": choose_action(snapshot, local, now, cfg),
               "awaiting_owner": awaiting_owner(snapshot, cfg, local),
               "main_red": [k for k in REPO_ORDER if snapshot["repos"][k].get("main_red")],
               "expired_verify": [{"repo": v.get("repo"), "pr": v.get("pr")} for v in local.get("pending_verify") or []
                                  if v.get("result") is None and statefile.verify_expired(v, now)],
               "stale_worktrees": list_stale_worktrees(snapshot, cfg),
               "digest_due": digest_due(local, cfg, now.astimezone())}
    except Exception as exc:  # noqa: BLE001 - the skill must always get JSON
        out = {"error": f"{type(exc).__name__}: {exc}", "snapshot": None, "awaiting_owner": [], "main_red": [],
               "expired_verify": [],
               "stale_worktrees": [], "digest_due": False,
               "action": _act("idle", None, None, {}, f"snapshot failed: {exc}")}
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
