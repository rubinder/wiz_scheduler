# factory/proposals.py
"""Turn findings into agent-proposed issue candidates, deduplicated against the owner's issues.

Proposals become PUBLIC issues, so only two kinds of input ever qualify:

- architecture allowlist entries with ``issue: null`` (backend and frontend);
- ``state.json`` findings that carry ``"public": true`` and a ``source`` in
  ``PUBLIC_SOURCES``.

Every other finding is held back: written to a local file for the owner and
never filed. Titles and bodies are built from validated fields only, with
``<`` escaped so no tag or HTML comment (a gate marker, the wizbot marker)
can be forged.

CLI (used by .claude/workflows/factory-propose.js):

    plan   --app-root A --issues APP.json --issues MKT.json --owner LOGIN --max N
           --slug app=OWNER/NAME --slug marketing=OWNER/NAME
           --out PLAN.json --held-out HELD.md --files-prefix PREFIX
    settle --plan PLAN.json --filed 0,2
    mark-checked

``plan`` reads findings from state.json without consuming them and writes, per
candidate i, ``PREFIX-i-repo.txt`` (the slug), ``PREFIX-i-title.txt`` and
``PREFIX-i-body.md`` (footer included). The workflow files issues from those
files only, so no agent ever produces or relays candidate text. ``settle``
removes only the findings that were filed, already had an owner issue, or
were held back; anything not filed stays for the next run, and a run that
dies midway loses nothing. A run that files nothing marks today as checked,
so state.py does not choose Propose again until tomorrow; ``mark-checked`` does
the same for a run that failed before it could settle.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable

from factory import statefile

_PATTERN = {"router-imports-router": "thin-router", "model-imports-service": "thin-router",
            "stripe-outside-billing": "fail-closed", "archived-at-outside-archive": "archive-never-delete",
            "fetch-outside-api": "anti-corruption-layer", "date-only-string-parse": "location-local-time",
            "frontend-availability-eval": "single-evaluator"}

PUBLIC_SOURCES = ("main-ci-red", "retro-followup")
REPOS = ("app", "marketing")
MAX_PER_RUN = 3
TITLE_MAX = 120
BODY_MAX = 4000
MARKER = "<!-- wizbot -->"

# Printable, single line, no tag or code-span characters. Mirrored in factory-propose.js.
KEY_RE = re.compile(r"^[^\x00-\x1f\x7f<>`\\]{1,200}$")
_RULE_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_FILE_RE = re.compile(r"^[A-Za-z0-9._/@+()\[\]-]{1,200}$")
_DETAIL_RE = re.compile(r"^[^\x00-\x1f\x7f`]{1,120}$")
_CTRL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_KEY_LINE_RE = re.compile(r"^factory-key: (.+)$", re.M)
_SLUG_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def _esc(text: str) -> str:
    return text.replace("<", "&lt;")


def _norm(title: str) -> str:
    return re.sub(r"\s+", " ", title.strip().lower())


def _fingerprint(finding: Any) -> str:
    return hashlib.sha256(json.dumps(finding, sort_keys=True).encode()).hexdigest()


def _login(issue: dict[str, Any]) -> str:
    author = issue.get("author")
    if isinstance(author, dict):
        author = author.get("login")
    return str(author or "").lower()


def factory_keys(bodies: Iterable[str]) -> set[str]:
    """Every ``factory-key:`` line in these issue bodies."""
    return {k.strip() for k in _KEY_LINE_RE.findall("\n".join(str(b or "") for b in bodies))}


def dedupe(candidates: list[dict[str, Any]], issues: list[dict[str, Any]],
           owner: str | None = None) -> list[dict[str, Any]]:
    """Drop candidates an existing issue already covers, and repeats within the list.

    A candidate is covered by an OPEN issue with the same normalised title, or
    by an issue in ANY state carrying its ``factory-key:`` line, so a cleanup
    the owner closed as won't-fix stays closed. With ``owner`` set, only the
    owner's issues count: anyone can open an issue on the public repo, and a
    stranger's issue must not suppress a proposal by squatting its title or key.
    """
    rows = [i for i in issues if isinstance(i, dict)]
    if owner is not None:
        rows = [i for i in rows if _login(i) == owner.lower()]
    titles = {_norm(str(i.get("title") or "")) for i in rows if str(i.get("state") or "OPEN").upper() == "OPEN"}
    keys = factory_keys(i.get("body") for i in rows)
    out: list[dict[str, Any]] = []
    for c in candidates:
        if _norm(c["title"]) in titles or c["key"] in keys:
            continue
        titles.add(_norm(c["title"]))
        keys.add(c["key"])
        out.append(c)
    return out


def from_allowlists(app_root: Path) -> list[dict[str, Any]]:
    """One candidate per allowlist entry with ``issue: null``; malformed entries are skipped."""
    out: list[dict[str, Any]] = []
    for path, prefix in ((app_root / "tests" / "architecture_allowlist.json", ""),
                         (app_root / "frontend" / "src" / "architecture_allowlist.json", "frontend/src/")):
        if not path.exists():
            continue
        for e in json.loads(path.read_text()):
            if not isinstance(e, dict) or e.get("issue") is not None:
                continue
            rule, file, detail = (str(e.get(k) or "") for k in ("rule", "file", "detail"))
            f = prefix + file
            if not (_RULE_RE.match(rule) and _FILE_RE.match(f) and ".." not in f and _DETAIL_RE.match(detail)):
                continue
            key = _esc(f"arch:{rule}:{f}:{detail}")
            if not KEY_RE.match(key):
                continue
            pattern = _PATTERN.get(rule, rule)
            # The short detail hash keeps titles distinct per (rule, file, detail), so
            # two entries for one file never dedupe each other by title.
            tag = hashlib.sha256(detail.encode()).hexdigest()[:6]
            out.append({
                "repo": "app",
                "key": key,
                "title": _esc(f"Architecture cleanup: {rule} in {f} ({tag})"),
                "body": (f"`{f}` violates `{rule}` ({_esc(detail)}), pattern `{pattern}` in "
                         f".claude/skills/architecture/SKILL.md.\n\nFix it and delete its entry from the allowlist "
                         f"in the same PR; the ratchet test fails until both are done."),
                "finding_index": None,
            })
    return out


def _finding_problem(f: Any) -> str | None:
    if not isinstance(f, dict):
        return "not an object"
    if f.get("public") is not True:
        return "not marked public: true"
    if f.get("source") not in PUBLIC_SOURCES:
        return f"source {f.get('source')!r} is not one of {', '.join(PUBLIC_SOURCES)}"
    if f.get("repo") not in REPOS:
        return f"repo {f.get('repo')!r} is not app or marketing"
    key, title, body = f.get("key"), f.get("title"), f.get("body", "")
    if not isinstance(key, str) or not KEY_RE.match(key) or key != key.strip():
        return "key is missing or malformed"
    if not isinstance(title, str) or not title.strip() or len(title) > TITLE_MAX or _CTRL_RE.search(title) or "\n" in title:
        return "title is missing, too long or not one line"
    if not isinstance(body, str) or len(body) > BODY_MAX:
        return "body is not a string of at most 4000 characters"
    return None


def from_findings(findings: list[Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(candidates, held). Held entries are ``{"finding", "reason"}`` and are never filed."""
    cands: list[dict[str, Any]] = []
    held: list[dict[str, Any]] = []
    for i, f in enumerate(findings):
        problem = _finding_problem(f)
        if problem:
            held.append({"finding": f, "reason": problem, "index": i})
            continue
        lines = [ln for ln in _CTRL_RE.sub("", f.get("body") or "").replace("\r", "").split("\n")
                 if not ln.lstrip().lower().startswith("factory-key:")]
        body = _esc("\n".join(lines).strip())
        cands.append({"repo": f["repo"], "key": f["key"], "title": _esc(f["title"].strip()),
                      "body": f"{body}\n\nSource: {f['source']}".strip(), "finding_index": i})
    return cands, held


def issue_body(candidate: dict[str, Any]) -> str:
    """The exact issue body filed for a candidate: its text, the key line, the marker."""
    return f"{candidate['body']}\n\nfactory-key: {candidate['key']}\n\n{MARKER}\n"


def _held_markdown(held: list[dict[str, Any]]) -> str:
    out = ["# Findings held back from the public repo", "",
           "These were not filed as issues: proposals are public, and only findings marked",
           f"`public: true` with a source in {', '.join(PUBLIC_SOURCES)} are filed.", ""]
    for h in held:
        out += [f"## {h['reason']}", "", "```json", json.dumps(h["finding"], indent=2, sort_keys=True), "```", ""]
    return "\n".join(out)


def plan(app_root: Path, issues: list[dict[str, Any]], findings: list[Any], owner: str,
         max_n: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """(plan, held). The plan lists at most ``max_n`` candidates to file."""
    finding_cands, held = from_findings(findings)
    allc = from_allowlists(app_root) + finding_cands
    kept = dedupe(allc, issues, owner=owner)
    kept_ids = {id(c) for c in kept}
    drop = [_fingerprint(findings[h["index"]]) for h in held]
    drop += [_fingerprint(findings[c["finding_index"]]) for c in allc
             if c["finding_index"] is not None and id(c) not in kept_ids]
    cands = []
    for n, c in enumerate(kept[:max_n]):
        fi = c["finding_index"]
        cands.append({"index": n, "repo": c["repo"], "key": c["key"], "title": c["title"], "body": c["body"],
                      "finding_fp": None if fi is None else _fingerprint(findings[fi])})
    return {"max": max_n, "candidates": cands, "deferred": max(0, len(kept) - max_n), "held": len(held),
            "drop_fps": drop}, held


def write_candidate_files(doc: dict[str, Any], prefix: str, slugs: dict[str, str]) -> None:
    """Write PREFIX-i-{repo.txt,title.txt,body.md} per candidate; remove stale ones first."""
    for i in range(MAX_PER_RUN):
        for suffix in ("repo.txt", "title.txt", "body.md"):
            Path(f"{prefix}-{i}-{suffix}").unlink(missing_ok=True)
    for c in doc["candidates"]:
        i = c["index"]
        Path(f"{prefix}-{i}-repo.txt").write_text(slugs[c["repo"]])
        Path(f"{prefix}-{i}-title.txt").write_text(c["title"])
        Path(f"{prefix}-{i}-body.md").write_text(issue_body(c))


def settle(plan_doc: dict[str, Any], filed: list[int], state_path: Path | None = None) -> int:
    """Remove filed, already-covered and held findings from state; keep the rest.

    When nothing was filed, today is marked checked so Propose is not chosen
    again until tomorrow. Returns how many findings were removed.
    """
    remove = set(plan_doc.get("drop_fps") or [])
    for c in plan_doc.get("candidates") or []:
        if c.get("index") in filed and c.get("finding_fp"):
            remove.add(c["finding_fp"])
    s = statefile.load(state_path)
    before = s.get("findings") or []
    s["findings"] = [f for f in before if _fingerprint(f) not in remove]
    removed = len(before) - len(s["findings"])
    if removed:
        statefile.event(s, "findings_settled", removed=removed)
    if not filed:
        _stamp_checked(s)
    statefile.save(s, state_path)
    return removed


def _stamp_checked(s: dict[str, Any]) -> None:
    s["proposals"] = {**(s.get("proposals") or {}), "checked": statefile._now()[:10]}


def mark_checked(state_path: Path | None = None) -> None:
    """Mark today checked, so Propose is not chosen again until tomorrow (used when a run fails)."""
    s = statefile.load(state_path)
    _stamp_checked(s)
    statefile.save(s, state_path)


def _max_arg(value: str) -> int:
    if not value.isascii() or not value.isdigit() or not 1 <= int(value) <= MAX_PER_RUN:
        raise argparse.ArgumentTypeError(f"--max must be an integer from 1 to {MAX_PER_RUN}")
    return int(value)


def _filed_arg(value: str) -> list[int]:
    if not re.fullmatch(r"(?:\d+(?:,\d+)*)?", value):
        raise argparse.ArgumentTypeError("--filed must be comma-separated candidate indices")
    return [int(v) for v in value.split(",") if v]


def _slug_arg(value: str) -> tuple[str, str]:
    repo, _, slug = value.partition("=")
    if repo not in REPOS or not _SLUG_RE.match(slug):
        raise argparse.ArgumentTypeError("--slug must be app=OWNER/NAME or marketing=OWNER/NAME")
    return repo, slug


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="factory.proposals")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan")
    p.add_argument("--app-root", required=True, type=Path)
    p.add_argument("--issues", required=True, type=Path, action="append",
                   help="JSON array of issues (title, body, author, state); repeat per list")
    p.add_argument("--owner", required=True)
    p.add_argument("--max", required=True, type=_max_arg)
    p.add_argument("--slug", required=True, type=_slug_arg, action="append")
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--held-out", required=True, type=Path)
    p.add_argument("--files-prefix", required=True)
    s = sub.add_parser("settle")
    s.add_argument("--plan", required=True, type=Path)
    s.add_argument("--filed", required=True, type=_filed_arg)
    sub.add_parser("mark-checked")
    a = ap.parse_args(argv)

    if a.cmd == "mark-checked":
        mark_checked()
        return 0

    if a.cmd == "plan":
        slugs = dict(a.slug)
        if set(slugs) != set(REPOS):
            ap.error("--slug is needed for both app and marketing")
        issues: list[dict[str, Any]] = []
        for path in a.issues:
            rows = json.loads(path.read_text())
            if not isinstance(rows, list):
                raise SystemExit(f"{path} is not a JSON array of issues")
            issues += rows
        findings = statefile.load().get("findings") or []
        doc, held = plan(a.app_root, issues, findings, a.owner, a.max)
        a.out.write_text(json.dumps(doc, indent=2))
        write_candidate_files(doc, a.files_prefix, slugs)
        if held:
            a.held_out.write_text(_held_markdown(held))
        elif a.held_out.exists():
            a.held_out.unlink()
        print(json.dumps({"candidates": len(doc["candidates"]), "deferred": doc["deferred"], "held": doc["held"]}))
        return 0
    removed = settle(json.loads(a.plan.read_text()), a.filed)
    print(json.dumps({"removed": removed}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
