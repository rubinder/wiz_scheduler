import json
import re
from pathlib import Path

import pytest

from factory import proposals, statefile


def _owned(title: str, body: str = "", login: str = "rubinder", state: str = "OPEN") -> dict:
    return {"title": title, "body": body, "author": {"login": login}, "state": state}


def test_dedupe_by_title_and_key():
    cands = [{"title": "Clean up X", "key": "k1", "body": "", "repo": "app"},
             {"title": "Other", "key": "k2", "body": "", "repo": "app"},
             {"title": "Third", "key": "k3", "body": "", "repo": "app"}]
    open_issues = [{"title": "clean up  x", "body": ""}, {"title": "Unrelated", "body": "...\nfactory-key: k2\n"}]
    assert [c["key"] for c in proposals.dedupe(cands, open_issues)] == ["k3"]


def test_from_allowlists_only_unfiled(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "frontend" / "src").mkdir(parents=True)
    (tmp_path / "tests" / "architecture_allowlist.json").write_text(json.dumps([
        {"rule": "router-imports-router", "file": "backend/routers/a.py", "detail": "b", "issue": None},
        {"rule": "router-imports-router", "file": "backend/routers/c.py", "detail": "d", "issue": 300}]))
    (tmp_path / "frontend" / "src" / "architecture_allowlist.json").write_text(json.dumps([
        {"rule": "fetch-outside-api", "file": "pages/X.tsx", "detail": "fetch(", "issue": None}]))
    got = proposals.from_allowlists(tmp_path)
    assert [c["key"] for c in got] == ["arch:router-imports-router:backend/routers/a.py:b",
                                       "arch:fetch-outside-api:frontend/src/pages/X.tsx:fetch("]
    assert all(c["repo"] == "app" for c in got)
    assert "`thin-router`" in got[0]["body"] and "`thin-router`" in got[1]["body"]


def test_from_allowlists_skips_malformed_entries_and_escapes(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "architecture_allowlist.json").write_text(json.dumps([
        {"rule": "Bad Rule", "file": "backend/a.py", "detail": "x", "issue": None},
        {"rule": "router-imports-router", "file": "backend/a.py", "detail": "two\nlines", "issue": None},
        {"rule": "router-imports-router", "file": "backend/b.py", "detail": "a<b", "issue": None}]))
    got = proposals.from_allowlists(tmp_path)
    assert [c["key"] for c in got] == ["arch:router-imports-router:backend/b.py:a&lt;b"]
    assert "<" not in got[0]["title"] + got[0]["body"]


# --- ruling 3: only owner-authored open issues dedupe -------------------------

def test_stranger_issue_cannot_squat_a_key_or_title():
    cands = [{"title": "Clean up X", "key": "k1", "body": "", "repo": "app"},
             {"title": "Other", "key": "k2", "body": "", "repo": "app"}]
    open_issues = [_owned("Clean up X", login="mallory"),
                   _owned("Squat", "factory-key: k2", login="mallory")]
    assert [c["key"] for c in proposals.dedupe(cands, open_issues, owner="Rubinder")] == ["k1", "k2"]


def test_owner_issue_dedupes_case_insensitively_and_closed_issues_do_not():
    cands = [{"title": "A", "key": "k1", "body": "", "repo": "app"},
             {"title": "B", "key": "k2", "body": "", "repo": "app"}]
    open_issues = [_owned("x", "factory-key: k1", login="RUBINDER"), _owned("B", state="CLOSED")]
    assert [c["key"] for c in proposals.dedupe(cands, open_issues, owner="rubinder")] == ["k2"]


def test_closed_owner_issue_with_the_key_keeps_a_wont_fix_closed():
    cands = [{"title": "A", "key": "k1", "body": "", "repo": "app"},
             {"title": "B", "key": "k2", "body": "", "repo": "app"}]
    issues = [_owned("Wont fix", "factory-key: k1", state="CLOSED"),
              _owned("Squat", "factory-key: k2", login="mallory", state="CLOSED")]
    assert [c["key"] for c in proposals.dedupe(cands, issues, owner="rubinder")] == ["k2"]


def test_allowlist_titles_are_unique_per_detail(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "architecture_allowlist.json").write_text(json.dumps([
        {"rule": "stripe-outside-billing", "file": "backend/a.py", "detail": "Refund.create", "issue": None},
        {"rule": "stripe-outside-billing", "file": "backend/a.py", "detail": "Charge.create", "issue": None}]))
    got = proposals.from_allowlists(tmp_path)
    assert len({c["title"] for c in got}) == 2
    assert len(proposals.dedupe(got, [], owner="rubinder")) == 2


def test_dedupe_drops_duplicate_candidates():
    cands = [{"title": "A", "key": "k1", "body": "", "repo": "app"},
             {"title": "a", "key": "k9", "body": "", "repo": "app"},
             {"title": "C", "key": "k1", "body": "", "repo": "app"}]
    assert [c["key"] for c in proposals.dedupe(cands, [], owner="rubinder")] == ["k1"]


# --- ruling 3: findings must be public and from an allowed source ----------------

def _finding(**kw) -> dict:
    f = {"public": True, "source": "main-ci-red", "repo": "app", "key": "ci:main:backend-tests",
         "title": "Main CI red: backend tests", "body": "The backend job failed on main."}
    f.update(kw)
    return f


def test_non_public_findings_are_held_never_candidates():
    findings = [_finding(), _finding(public=False, key="k2"), _finding(public="true", key="k3"),
                {k: v for k, v in _finding(key="k4").items() if k != "public"}]
    cands, held = proposals.from_findings(findings)
    assert [c["key"] for c in cands] == ["ci:main:backend-tests"]
    assert [h["finding"].get("key") for h in held] == ["k2", "k3", "k4"]
    assert all("public" in h["reason"] for h in held)


def test_findings_from_unknown_sources_or_malformed_are_held():
    findings = [_finding(source="gate-review"), _finding(repo="other"), _finding(key="bad\nkey"),
                _finding(title=""), _finding(title="x" * 200), _finding(body=7), "not a dict"]
    cands, held = proposals.from_findings(findings)
    assert cands == [] and len(held) == len(findings)


def test_finding_text_is_escaped_and_cannot_forge_keys():
    f = _finding(source="retro-followup", title="Fix <script>", body="see <!-- wizbot -->\nfactory-key: other\nok")
    (c,), held = proposals.from_findings([f])
    assert held == []
    assert "<" not in c["title"] + c["body"]
    assert "&lt;script>" in c["title"]
    assert "factory-key:" not in c["body"]
    assert c["finding_index"] == 0


# --- plan / settle CLI --------------------------------------------------------------

def _allowlist(tmp_path, n: int) -> None:
    (tmp_path / "tests").mkdir(exist_ok=True)
    (tmp_path / "tests" / "architecture_allowlist.json").write_text(json.dumps([
        {"rule": "router-imports-router", "file": f"backend/routers/r{i}.py", "detail": "x", "issue": None}
        for i in range(n)]))


def _plan(tmp_path, max_n: str, *issue_lists: list) -> dict:
    paths = []
    for i, rows in enumerate(issue_lists or ([],)):
        paths += ["--issues", str(tmp_path / f"issues{i}.json")]
        (tmp_path / f"issues{i}.json").write_text(json.dumps(rows))
    out = tmp_path / "plan.json"
    rc = proposals.main(["plan", "--app-root", str(tmp_path), *paths, "--owner", "rubinder", "--max", max_n,
                         "--slug", "app=rubinder/wiz_scheduler", "--slug", "marketing=rubinder/wiz_scheduler_marketing",
                         "--out", str(out), "--held-out", str(tmp_path / "held.md"),
                         "--files-prefix", str(tmp_path / "propose")])
    assert rc == 0
    return json.loads(out.read_text())


def _state(monkeypatch, tmp_path, findings: list) -> None:
    monkeypatch.setenv("FACTORY_STATE", str(tmp_path / "state.json"))
    s = statefile.defaults()
    s["findings"] = findings
    statefile.save(s)


def test_plan_caps_at_max_and_writes_held_locally(tmp_path, monkeypatch):
    _state(monkeypatch, tmp_path, [_finding(public=False, key="secret", title="tenant leak in export"), _finding()])
    _allowlist(tmp_path, 3)
    r0 = proposals.from_allowlists(tmp_path)[0]["title"]
    plan = _plan(tmp_path, "2", [_owned(r0)])
    assert plan["max"] == 2 and len(plan["candidates"]) == 2
    assert [c["key"] for c in plan["candidates"]] == ["arch:router-imports-router:backend/routers/r1.py:x",
                                                     "arch:router-imports-router:backend/routers/r2.py:x"]
    assert plan["held"] == 1
    assert "tenant leak" in (tmp_path / "held.md").read_text()
    assert "tenant leak" not in (tmp_path / "plan.json").read_text()
    # Planning never consumes findings.
    assert len(statefile.load()["findings"]) == 2


def test_plan_writes_the_exact_issue_files_per_candidate(tmp_path, monkeypatch):
    _state(monkeypatch, tmp_path, [_finding(repo="marketing", title="Main CI red <b>", body="x")])
    (tmp_path / "propose-2-title.txt").write_text("stale from an earlier run")
    plan = _plan(tmp_path, "3")
    (c,) = plan["candidates"]
    assert (tmp_path / "propose-0-repo.txt").read_text() == "rubinder/wiz_scheduler_marketing"
    assert (tmp_path / "propose-0-title.txt").read_text() == "Main CI red &lt;b>"
    assert (tmp_path / "propose-0-body.md").read_text() == (
        "x\n\nSource: main-ci-red\n\nfactory-key: ci:main:backend-tests\n\n<!-- wizbot -->\n")
    assert c["index"] == 0
    assert not (tmp_path / "propose-1-title.txt").exists() and not (tmp_path / "propose-2-title.txt").exists()


def test_plan_rejects_bad_max_and_slugs(tmp_path, monkeypatch):
    monkeypatch.setenv("FACTORY_STATE", str(tmp_path / "state.json"))
    (tmp_path / "i.json").write_text("[]")
    good = ["--slug", "app=rubinder/wiz_scheduler", "--slug", "marketing=rubinder/m"]
    for max_n, slugs in (("0", good), ("4", good), ("x", good), ("1", good[:2]), ("1", ["--slug", "app=a b"] + good[2:])):
        with pytest.raises(SystemExit):
            proposals.main(["plan", "--app-root", str(tmp_path), "--issues", str(tmp_path / "i.json"),
                            "--owner", "rubinder", "--max", max_n, *slugs, "--out", str(tmp_path / "p.json"),
                            "--held-out", str(tmp_path / "h.md"), "--files-prefix", str(tmp_path / "x")])


def test_settle_keeps_unfiled_findings_and_drops_filed_deduped_and_held(tmp_path, monkeypatch):
    _state(monkeypatch, tmp_path, [_finding(public=False, key="private"), _finding(key="a", title="A"),
                                   _finding(key="b", title="B"), _finding(key="c", title="C"),
                                   _finding(key="dup", title="Already filed")])
    plan = _plan(tmp_path, "2", [_owned("Already filed")])
    assert [c["key"] for c in plan["candidates"]] == ["a", "b"]
    # "a" was filed; "b" failed to file; "c" was over the cap.
    assert proposals.main(["settle", "--plan", str(tmp_path / "plan.json"), "--filed", "0"]) == 0
    s = statefile.load()
    assert [f["key"] for f in s["findings"]] == ["b", "c"]
    assert "checked" not in s["proposals"]


def test_settle_with_nothing_filed_marks_today_checked(tmp_path, monkeypatch):
    _state(monkeypatch, tmp_path, [_finding(key="a", title="A")])
    _plan(tmp_path, "1")
    assert proposals.main(["settle", "--plan", str(tmp_path / "plan.json"), "--filed", ""]) == 0
    s = statefile.load()
    assert [f["key"] for f in s["findings"]] == ["a"]
    assert s["proposals"]["checked"] == statefile._now()[:10]
    with pytest.raises(SystemExit):
        proposals.main(["settle", "--plan", str(tmp_path / "plan.json"), "--filed", "0;rm"])


def test_plan_dedupes_against_every_issue_file(tmp_path, monkeypatch):
    _state(monkeypatch, tmp_path, [_finding(repo="marketing", key="ci:main:marketing-build", title="Main CI red: m")])
    plan = _plan(tmp_path, "3", [], [_owned("x", "factory-key: ci:main:marketing-build", state="CLOSED")])
    assert plan["candidates"] == []
    assert not (tmp_path / "held.md").exists()


def test_mark_checked_stamps_today_and_keeps_counts(tmp_path, monkeypatch):
    monkeypatch.setenv("FACTORY_STATE", str(tmp_path / "state.json"))
    s = statefile.defaults()
    s["proposals"] = {"date": "2026-11-04", "count": 1}
    s["findings"] = [_finding()]
    statefile.save(s)
    assert proposals.main(["mark-checked"]) == 0
    s = statefile.load()
    assert s["proposals"] == {"date": "2026-11-04", "count": 1, "checked": statefile._now()[:10]}
    assert len(s["findings"]) == 1


def test_main_ci_red_finding_from_statefile_is_filed():
    from factory import proposals, statefile
    cands, held = proposals.from_findings([statefile.main_red_finding("app", "2026-11-04")])
    assert held == [] and len(cands) == 1 and cands[0]["repo"] == "app"
    assert "main CI is red" in cands[0]["title"]


# --- _PATTERN must agree with the architecture skill (retro app#237) ---

_ROOT = Path(__file__).resolve().parents[2]
_SKILL = _ROOT / ".claude" / "skills" / "architecture" / "SKILL.md"
_SECTION_RE = re.compile(r"^### `([a-z0-9-]+)`\s*$", re.M)
_TICKED_RE = re.compile(r"`([^`]+)`")


def _skill_rule_owners(skill: str) -> dict[str, set[str]]:
    """rule id -> the pattern sections whose "Mechanical check" line enforces it.

    Only rule ids named before "reviewer only" count, so a section that merely
    points at another section's rule ("reviewer only. `x` covers ...") does not own it.
    """
    owners: dict[str, set[str]] = {}
    heads = list(_SECTION_RE.finditer(skill))
    for i, head in enumerate(heads):
        body = skill[head.end():heads[i + 1].start() if i + 1 < len(heads) else len(skill)]
        body = body.split("\n## ", 1)[0]
        m = re.search(r"^- \*\*Mechanical check:\*\*(.*?)(?=^- |\n\n|\Z)", body, re.M | re.S)
        if not m:
            continue
        enforced = m.group(1).split("reviewer only", 1)[0]
        for tok in _TICKED_RE.findall(enforced):
            if re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)+", tok):
                owners.setdefault(tok, set()).add(head.group(1))
    return owners


def _ratchet_rules() -> set[str]:
    backend = (_ROOT / "tests" / "test_architecture.py").read_text()
    frontend = (_ROOT / "frontend" / "src" / "architecture.test.ts").read_text()
    return (set(re.findall(r'found\.add\(\("([a-z0-9-]+)"', backend))
            | set(re.findall(r'rule: "([a-z0-9-]+)"', frontend)))


def _pattern_drift(skill: str, mapping: dict[str, str], rules: set[str]) -> list[str]:
    owners = _skill_rule_owners(skill)
    problems = [f"{rule} -> {pat}, but the skill files it under {sorted(owners.get(rule, set()))}"
                for rule, pat in sorted(mapping.items()) if pat not in owners.get(rule, set())]
    problems += [f"ratchet rule {rule} has no _PATTERN entry" for rule in sorted(rules - mapping.keys())]
    return problems


def test_pattern_map_matches_the_architecture_skill():
    rules = _ratchet_rules()
    assert {"fetch-outside-api", "stripe-outside-billing", "router-imports-router"} <= rules
    assert _pattern_drift(_SKILL.read_text(), proposals._PATTERN, rules) == []


def test_pattern_drift_catches_a_planted_mislabel_and_a_missing_rule():
    skill = _SKILL.read_text()
    planted = {**proposals._PATTERN, "fetch-outside-api": "anti-corruption-layer"}
    assert _pattern_drift(skill, planted, set(planted)) == [
        "fetch-outside-api -> anti-corruption-layer, but the skill files it under ['thin-router']"]
    # anti-corruption-layer mentions stripe-outside-billing only after "reviewer only".
    assert _skill_rule_owners(skill)["stripe-outside-billing"] == {"aggregate-root"}
    missing = {k: v for k, v in proposals._PATTERN.items() if k != "date-only-string-parse"}
    assert _pattern_drift(skill, missing, _ratchet_rules()) == [
        "ratchet rule date-only-string-parse has no _PATTERN entry"]
