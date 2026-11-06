# factory/tests/test_statefile.py
from datetime import datetime, timedelta, timezone

from factory import statefile


def test_load_missing_file_gives_defaults(tmp_path):
    s = statefile.load(tmp_path / "state.json")
    assert s["paused_until"] is None and s["pending_verify"] == [] and s["notified"] == []


def test_save_then_load_round_trips(tmp_path):
    p = tmp_path / "state.json"
    s = statefile.load(p)
    s["paused_until"] = "2026-11-04T13:00:00Z"
    statefile.save(s, p)
    assert statefile.load(p)["paused_until"] == "2026-11-04T13:00:00Z"
    assert not list(tmp_path.glob("*.tmp"))


def test_cli_add_verify_then_verified(tmp_path, monkeypatch):
    p = tmp_path / "state.json"
    monkeypatch.setenv("FACTORY_STATE", str(p))
    statefile.main(["add-verify", "app", "240", "abc123"])
    [entry] = statefile.load(p)["pending_verify"]
    assert {k: v for k, v in entry.items() if k != "at"} == {"repo": "app", "pr": 240, "sha": "abc123", "result": None}
    assert not statefile.verify_expired(entry, datetime.now(timezone.utc))
    statefile.main(["verified", "app", "240", "pass"])
    assert statefile.load(p)["pending_verify"] == []


def test_cli_verified_fail_keeps_a_record_for_the_digest(tmp_path, monkeypatch):
    p = tmp_path / "state.json"
    monkeypatch.setenv("FACTORY_STATE", str(p))
    statefile.main(["add-verify", "app", "240", "abc123"])
    statefile.main(["verified", "app", "240", "fail"])
    s = statefile.load(p)
    assert s["pending_verify"] == []
    assert s["events"][-1]["kind"] == "smoke_fail"


def test_cli_gate_pass_then_has_gate_pass(tmp_path, monkeypatch):
    p = tmp_path / "state.json"
    monkeypatch.setenv("FACTORY_STATE", str(p))
    sha = "A" * 40
    assert statefile.main(["has-gate-pass", "app", "12", sha]) == 1
    assert statefile.main(["gate-pass", "app", "12", sha]) == 0
    assert statefile.load(p)["gate_passes"] == [{"repo": "app", "pr": 12, "sha": "a" * 40}]
    assert statefile.main(["has-gate-pass", "app", "12", sha]) == 0
    assert statefile.main(["has-gate-pass", "app", "12", "b" * 40]) == 1
    assert statefile.main(["has-gate-pass", "marketing", "12", sha]) == 1
    assert statefile.main(["has-gate-pass", "app", "13", sha]) == 1


def test_gate_passes_keep_the_last_500(tmp_path, monkeypatch):
    p = tmp_path / "state.json"
    monkeypatch.setenv("FACTORY_STATE", str(p))
    s = statefile.load(p)
    s["gate_passes"] = [{"repo": "app", "pr": i, "sha": "c" * 40} for i in range(500)]
    statefile.save(s, p)
    statefile.main(["gate-pass", "app", "999", "d" * 40])
    passes = statefile.load(p)["gate_passes"]
    assert len(passes) == 500 and passes[0]["pr"] == 1 and passes[-1]["pr"] == 999


import pytest


@pytest.mark.parametrize("args", [
    ["other", "12", "a" * 40],
    ["app", "0", "a" * 40],
    ["app", "-3", "a" * 40],
    ["app", "x", "a" * 40],
    ["app", "12", "a" * 39],
    ["app", "12", "g" * 40],
    ["app", "12", "a" * 41],
    ["app", "12"],
    [],
])
def test_gate_pass_rejects_bad_input_and_writes_nothing(tmp_path, monkeypatch, args):
    p = tmp_path / "state.json"
    monkeypatch.setenv("FACTORY_STATE", str(p))
    assert statefile.main(["gate-pass", *args]) == 2
    assert not p.exists()


def test_verify_older_than_a_day_or_untimed_is_expired():
    now = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)
    assert not statefile.verify_expired({"at": "2026-11-03T12:30:00Z"}, now)
    assert statefile.verify_expired({"at": "2026-11-03T11:59:00Z"}, now)
    assert statefile.verify_expired({}, now)
    assert statefile.verify_expired({"at": "garbage"}, now)
    assert statefile.verify_expired({"at": "2026-11-04T11:00:00"}, now)  # naive


def test_verified_expired_drops_the_entry(tmp_path, monkeypatch):
    p = tmp_path / "state.json"
    monkeypatch.setenv("FACTORY_STATE", str(p))
    statefile.main(["add-verify", "app", "240", "abc123"])
    statefile.main(["verified", "app", "240", "expired"])
    s = statefile.load(p)
    assert s["pending_verify"] == [] and s["events"][-1]["kind"] == "verify_expired"


def test_attempts_count_per_kind_repo_pr_and_sha(tmp_path, monkeypatch, capsys):
    p = tmp_path / "state.json"
    monkeypatch.setenv("FACTORY_STATE", str(p))
    sha = "A" * 40
    for want in (1, 2, 3):
        assert statefile.main(["attempt", "merge", "app", "12", sha]) == 0
        assert capsys.readouterr().out.strip() == str(want)
    s = statefile.load(p)
    assert statefile.attempts(s, "merge", "app", 12, sha) == 3
    assert statefile.attempts(s, "merge", "app", 12, "b" * 40) == 0  # a new head starts again
    assert statefile.attempts(s, "fix", "app", 12, sha) == 0
    assert statefile.attempts(s, "merge", "marketing", 12, sha) == 0


@pytest.mark.parametrize("args", [["push", "app", "12", "a" * 40], ["merge", "x", "12", "a" * 40],
                                  ["merge", "app", "0", "a" * 40], ["merge", "app", "12", "abc"], ["merge"]])
def test_attempt_rejects_bad_input_and_writes_nothing(tmp_path, monkeypatch, args):
    p = tmp_path / "state.json"
    monkeypatch.setenv("FACTORY_STATE", str(p))
    assert statefile.main(["attempt", *args]) == 2
    assert not p.exists()


def test_main_red_records_one_public_finding_per_repo_per_day(tmp_path, monkeypatch, capsys):
    p = tmp_path / "state.json"
    monkeypatch.setenv("FACTORY_STATE", str(p))
    assert statefile.main(["main-red", "app"]) == 0 and capsys.readouterr().out.strip() == "new"
    assert statefile.main(["main-red", "app"]) == 0 and capsys.readouterr().out.strip() == "seen"
    assert statefile.main(["main-red", "marketing"]) == 0 and capsys.readouterr().out.strip() == "new"
    findings = statefile.load(p)["findings"]
    assert [f["repo"] for f in findings] == ["app", "marketing"]
    assert all(f["source"] == "main-ci-red" and f["public"] is True and f["title"] for f in findings)
    assert statefile.main(["main-red", "x; rm"]) == 2


def test_retried_records_one_reply(tmp_path, monkeypatch):
    monkeypatch.setenv("FACTORY_STATE", str(tmp_path / "s.json"))
    assert statefile.main(["retried", "app", "9", "2026-11-04T11:00:00Z"]) == 0
    assert statefile.load()["retried"] == ["app-9-2026-11-04T11:00:00Z"]
    assert statefile.main(["retried", "app", "9", "$(rm -rf ~)"]) == 2
    assert statefile.main(["retried", "evil", "9", "2026-11-04T11:00:00Z"]) == 2
