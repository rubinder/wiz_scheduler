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
