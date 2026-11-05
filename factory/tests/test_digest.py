import json
from datetime import datetime, timezone
from pathlib import Path

from factory import config, digest, statefile

CFG = config.load_config(Path(__file__).resolve().parents[1] / "config.example.toml")


def test_digest_sections_and_counts():
    local = statefile.defaults()
    local["events"] = [
        {"at": "2026-11-04T01:00:00Z", "kind": "pause", "until": "2026-11-04T03:00:00Z"},
        {"at": "2026-11-04T02:00:00Z", "kind": "gate", "gate": "accessibility", "result": "pass"},
        {"at": "2026-11-04T02:00:00Z", "kind": "gate", "gate": "accessibility", "result": "fail"},
        {"at": "2026-11-02T02:00:00Z", "kind": "gate", "gate": "i18n", "result": "fail"},
    ]
    rows = {"merged": [{"repo": "app", "number": 240, "title": "Add x"}],
            "awaiting": [{"repo": "app", "pr": 241, "title": "Migration y", "protected": ["backend/alembic/versions/1.py"]}],
            "blocked": [], "proposals": [{"repo": "app", "number": 250, "title": "Clean up z"}],
            "misses": []}
    md = digest.build(local, rows, "2026-11-04", since="2026-11-03T08:00:00Z")
    for heading in ["## Merged", "## Waiting on you", "## Blocked", "## Proposals", "## Misses", "## Usage-limit pauses", "## Gates"]:
        assert heading in md
    assert "app#240 Add x" in md and "app#241 Migration y" in md
    assert "accessibility: 1 pass, 1 fail" in md
    assert "i18n" not in md  # outside the window
    assert "- none" in md     # blocked and misses are empty


def test_gather_keeps_only_owner_authored_and_factory_branches():
    owner = CFG.owner_login
    seen = []

    def run(argv):
        seen.append(argv)
        j = " ".join(argv)
        if "pr list" in j and "merged" in j:
            return json.dumps([
                {"number": 1, "title": "mine", "headRefName": "agent/x", "author": {"login": owner}},
                {"number": 2, "title": "theirs", "headRefName": "agent/y", "author": {"login": "mallory"}},
                {"number": 3, "title": "human", "headRefName": "feat/z", "author": {"login": owner}},
            ])
        if "issue list" in j and "agent-blocked" in j:
            return json.dumps([{"number": 5, "title": "ok", "author": {"login": owner}},
                               {"number": 6, "title": "evil", "author": {"login": "mallory"}}])
        return "[]"

    rows = digest.gather(run, CFG, "2026-11-03T08:00:00Z")
    assert [r["title"] for r in rows["merged"] if r["repo"] == "app"] == ["mine"]
    assert [r["title"] for r in rows["blocked"] if r["repo"] == "app"] == ["ok"]
    assert set(rows) == {"merged", "awaiting", "blocked", "proposals", "misses"}


def test_main_write_and_sent(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FACTORY_STATE", str(tmp_path / "s.json"))
    monkeypatch.setattr(digest, "FACTORY_HOME", tmp_path)
    monkeypatch.setattr(digest, "load_config", lambda: CFG)
    monkeypatch.setattr(digest, "gather", lambda run, cfg, since: {
        "merged": [], "awaiting": [], "blocked": [], "proposals": [], "misses": []})
    assert digest.main(["write"]) == 0
    out = Path(capsys.readouterr().out.strip())
    assert out.parent == tmp_path / "digests" and out.read_text().startswith("# Factory digest")
    assert digest.main(["sent"]) == 0
    assert statefile.load(tmp_path / "s.json")["digest_sent"] == out.stem


def _retro(d, name, body):
    (d / name).write_text(body)


def test_misses_by_class_counts_recent_retros(tmp_path):
    for name, cls in [("2026-11-02-app-1.md", "missing-tenant-filter"), ("2026-11-03-app-2.md", "missing-tenant-filter"),
                      ("2026-10-01-app-3.md", "rtl-layout")]:
        (tmp_path / name).write_text(f'+++\nmiss = "app#1"\nkind = "revert"\nclass = "{cls}"\ngate = "x"\n+++\n')
    assert digest.misses_by_class(tmp_path, "2026-10-28") == {"missing-tenant-filter": 2}
    md = digest.build(statefile.defaults(), {"merged": [], "awaiting": [], "blocked": [], "proposals": [], "misses": []},
                      "2026-11-04", since="2026-11-03T08:00:00Z", weekly={"missing-tenant-filter": 2})
    assert "## Misses this week by class\n- missing-tenant-filter: 2" in md


def test_misses_by_class_skips_malformed_and_sanitizes(tmp_path):
    _retro(tmp_path, "2026-11-02-app-1.md", "no front matter here\n")
    _retro(tmp_path, "2026-11-02-app-2.md", "+++\nclass = \n+++\n")          # invalid TOML
    _retro(tmp_path, "2026-11-02-app-3.md", "+++\nclass = \"x\"\n")          # unterminated
    _retro(tmp_path, "2026-11-02-app-4.md", "+++\nmiss = \"a#1\"\n+++\n")    # no class
    _retro(tmp_path, "2026-11-02-app-5.md", '+++\nclass = "Bad Class!\\n## x"\n+++\n')
    _retro(tmp_path, "2026-11-02-app-6.md", "+++\nclass = 7\n+++\n")
    _retro(tmp_path, "2026-11-02-app-7.md", '+++\nclass = "ok-class"\n+++\n')
    _retro(tmp_path, "notes.md", '+++\nclass = "ok-class"\n+++\n')           # no date prefix
    (tmp_path / "2026-11-02-app-8.md").write_bytes(b"+++\nclass = \"\xff\"\n+++\n")  # undecodable
    (tmp_path / "2026-11-02-app-9.md").mkdir()                              # unreadable
    assert digest.misses_by_class(tmp_path, "2026-10-28") == {"unclassified": 3, "ok-class": 1}


def test_build_weekly_section_only_when_given():
    rows = {"merged": [], "awaiting": [], "blocked": [], "proposals": [], "misses": []}
    absent = digest.build(statefile.defaults(), rows, "2026-11-04")
    assert "by class" not in absent
    empty = digest.build(statefile.defaults(), rows, "2026-11-04", weekly={})
    assert "## Misses this week by class\n- none" in empty


def test_gather_lists_stuck_prs_from_local_state(monkeypatch):
    from factory import state as st
    sha = "a" * 40
    p = dict(number=50, branch="agent/issue-7-x", checks="failure", labels=[], files=["backend/x.py"], gate_report="none",
             is_draft=False, title="t", head_sha=sha)
    snap = {"repos": {"app": {"prs": [p], "issues": []}, "marketing": {"prs": [], "issues": []}}, "misses": []}
    local = statefile.defaults()
    local["attempts"] = [{"kind": "fix", "repo": "app", "pr": 50, "sha": sha, "n": statefile.MAX_ATTEMPTS}]
    monkeypatch.setattr(st, "collect", lambda run, cfg, now, loc=None: snap)
    monkeypatch.setattr(digest.statefile, "load", lambda *a, **k: local)
    rows = digest.gather(lambda argv: "[]", CFG, "2026-11-03T08:00:00Z")
    assert [(r["pr"], r["reason"]) for r in rows["awaiting"]] == [(50, st.REASON_STUCK)]
