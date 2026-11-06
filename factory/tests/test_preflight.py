import os
import random
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from factory import config, preflight, statefile

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)
CFG = config.load_config(Path(__file__).resolve().parents[1] / "config.example.toml")


def _msg(kind, at, **extra):
    import json
    return json.dumps({"type": kind, "timestamp": at, **extra}) + "\n"


def probes(**kw):
    base = dict(on_battery=lambda: False, docker_up=lambda: True, lock_held=lambda: False,
                stop_file=lambda: False, latest_owner_activity=lambda: None)
    base.update(kw)
    return preflight.Probes(**base)


def test_all_clear_runs():
    assert preflight.reasons_to_skip(NOW, CFG, statefile.defaults(), probes()) == []


def test_each_precondition_skips():
    local = statefile.defaults()
    assert "on battery" in preflight.reasons_to_skip(NOW, CFG, local, probes(on_battery=lambda: True))
    assert "docker not running" in preflight.reasons_to_skip(NOW, CFG, local, probes(docker_up=lambda: False))
    assert "another tick holds the lock" in preflight.reasons_to_skip(NOW, CFG, local, probes(lock_held=lambda: True))
    assert "STOP file present" in preflight.reasons_to_skip(NOW, CFG, local, probes(stop_file=lambda: True))


def test_owner_active_within_idle_window():
    recent = (NOW - timedelta(minutes=5)).timestamp()
    old = (NOW - timedelta(minutes=30)).timestamp()
    assert "owner active" in preflight.reasons_to_skip(NOW, CFG, statefile.defaults(), probes(latest_owner_activity=lambda: recent))
    assert preflight.reasons_to_skip(NOW, CFG, statefile.defaults(), probes(latest_owner_activity=lambda: old)) == []


def test_paused_until_future_skips_past_runs():
    local = statefile.defaults()
    local["paused_until"] = "2026-11-04T13:00:00Z"
    assert any(r.startswith("paused until") for r in preflight.reasons_to_skip(NOW, CFG, local, probes()))
    local["paused_until"] = "2026-11-04T11:00:00Z"
    assert preflight.reasons_to_skip(NOW, CFG, local, probes()) == []


def test_parse_limit_epoch_form():
    epoch = int((NOW + timedelta(hours=2)).timestamp())
    assert preflight.parse_limit(f"Claude AI usage limit reached|{epoch}", NOW) == NOW + timedelta(hours=2)


def test_parse_limit_resets_clock_form():
    got = preflight.parse_limit("You've hit your limit · resets 3pm (UTC)", NOW)
    assert got == datetime(2026, 11, 4, 15, 0, tzinfo=timezone.utc)


def test_unrecognised_limit_text_pauses_one_hour():
    assert preflight.parse_limit("Error: usage limit reached, try later", NOW) == NOW + timedelta(hours=1)


def test_non_limit_output_is_none():
    assert preflight.parse_limit("idle — nothing to do", NOW) is None


def test_parse_limit_never_raises_on_arbitrary_input():
    rng = random.Random(1234)
    pieces = ["usage limit", "hit your limit", "limit reached|", "resets ", "99pm", "0am", "13:77pm",
              "(UTC)", "9" * 40, "\x00\xff", "�", "\n", "limit reached|99999999999999999999"]
    for _ in range(300):
        text = "".join(rng.choice(pieces) if rng.random() < 0.5 else chr(rng.randrange(0, 0x3000))
                       for _ in range(rng.randrange(0, 30)))
        got = preflight.parse_limit(text, NOW)
        assert got is None or isinstance(got, datetime)
    for text in ["usage limit reached|99999999999999999999", "usage limit resets 13pm", "usage limit resets 0:99am",
                 "usage limit resets 12:61pm (UTC)"]:
        assert isinstance(preflight.parse_limit(text, NOW), datetime)


def test_owner_transcripts_exclude_factory_projects(tmp_path):
    projects = tmp_path / "projects"
    (projects / "-Users-x-factory-wiz-scheduler").mkdir(parents=True)
    (projects / "-Users-x-IdeaProjects-wiz-scheduler").mkdir(parents=True)
    f1 = projects / "-Users-x-factory-wiz-scheduler" / "a.jsonl"
    f2 = projects / "-Users-x-IdeaProjects-wiz-scheduler" / "b.jsonl"
    f1.write_text(_msg("user", "2026-11-04T11:59:00Z"))
    f2.write_text(_msg("user", "2026-11-04T11:00:00Z"))
    got = preflight.latest_owner_activity(projects, factory_home=Path("/Users/x/factory"))
    assert got == datetime(2026, 11, 4, 11, 0, tzinfo=timezone.utc).timestamp()


def test_parse_limit_clamped_to_24h():
    assert preflight.parse_limit("usage limit reached|9999999999", NOW) == NOW + timedelta(hours=24)


def test_probe_timeouts_mean_not_ready(monkeypatch):
    import subprocess

    def boom(*a, **kw):
        assert kw.get("timeout")
        raise subprocess.TimeoutExpired(a[0], kw["timeout"])

    monkeypatch.setattr(preflight.subprocess, "run", boom)
    assert preflight.real_docker_up() is False
    assert preflight.real_on_battery() is True


def test_vanishing_transcript_is_ignored(tmp_path, monkeypatch):
    projects = tmp_path / "projects"
    (projects / "-p").mkdir(parents=True)
    ok = projects / "-p" / "a.jsonl"
    gone = projects / "-p" / "b.jsonl"
    ok.write_text(_msg("user", "2026-11-04T11:00:00Z"))
    gone.write_text(_msg("user", "2026-11-04T11:30:00Z"))
    real_stat = Path.stat

    def stat(self, *a, **kw):
        if self.name == "b.jsonl":
            raise FileNotFoundError(self)
        return real_stat(self, *a, **kw)

    monkeypatch.setattr(Path, "stat", stat)
    assert preflight.latest_owner_activity(projects, factory_home=Path("/zzz")) is not None


def test_factory_home_honours_env():
    import subprocess, sys
    out = subprocess.run([sys.executable, "-c", "from factory.config import FACTORY_HOME; print(FACTORY_HOME)"],
                         env={**os.environ, "FACTORY_HOME": "/tmp/fh-test"}, capture_output=True, text=True,
                         cwd=Path(__file__).resolve().parents[2])
    assert out.stdout.strip() == "/tmp/fh-test"


def test_unreadable_or_naive_paused_until_fails_closed():
    for bad in ["garbage", "2026-11-04T10:00:00", "2026-13-99T00:00:00Z", 12345]:
        local = statefile.defaults()
        local["paused_until"] = bad
        assert "paused_until unreadable" in preflight.reasons_to_skip(NOW, CFG, local, probes()), bad


def test_paused_until_with_offset_is_normalised():
    local = statefile.defaults()
    local["paused_until"] = "2026-11-04T14:00:00+02:00"  # 12:00Z, not after NOW
    assert preflight.reasons_to_skip(NOW, CFG, local, probes()) == []
    local["paused_until"] = "2026-11-04T15:00:00+02:00"  # 13:00Z
    assert any(r.startswith("paused until") for r in preflight.reasons_to_skip(NOW, CFG, local, probes()))


def test_malformed_quiet_hours_skips():
    import dataclasses
    for bad in [(1,), (1, 2, 3), ("a", "b"), (-1, 5), (3, 99)]:
        cfg = dataclasses.replace(CFG, quiet_hours=bad)
        assert "quiet_hours malformed" in preflight.reasons_to_skip(NOW, cfg, statefile.defaults(), probes()), bad


def test_check_fails_closed_on_any_exception(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(preflight, "ERROR_STAMP", tmp_path / "preflight-error-notified")
    monkeypatch.setattr(preflight, "_osascript", lambda argv: 0)

    def boom(*a, **kw):
        raise KeyError("x")
    monkeypatch.setattr(preflight, "load_config", boom)
    assert preflight.main(["check"]) == 3
    assert capsys.readouterr().out.strip() == "skip: preflight error KeyError"
    monkeypatch.setattr(preflight, "load_config", lambda: CFG)
    monkeypatch.setattr(preflight.statefile, "load", boom)
    assert preflight.main(["check"]) == 3
    monkeypatch.setattr(preflight.statefile, "load", lambda: statefile.defaults())
    monkeypatch.setattr(preflight, "_real_probes", boom)
    assert preflight.main(["check"]) == 3


def test_preflight_error_notifies_owner_once_per_day_without_state(monkeypatch, tmp_path):
    sent: list[list[str]] = []
    stamp = tmp_path / "preflight-error-notified"
    monkeypatch.setattr(preflight, "ERROR_STAMP", stamp)
    monkeypatch.setattr(preflight, "_osascript", lambda argv: sent.append(argv) or 0)

    def corrupt(*a, **kw):
        raise ValueError("Expecting value: line 1 column 1")
    monkeypatch.setattr(preflight, "load_config", lambda: CFG)
    monkeypatch.setattr(preflight.statefile, "load", corrupt)
    monkeypatch.setattr(preflight.statefile, "save", corrupt)  # state.json is never touched
    assert preflight.main(["check"]) == 3
    assert preflight.main(["check"]) == 3
    assert len(sent) == 1 and sent[0][0] == "osascript" and "ValueError" in sent[0][-1]
    assert stamp.read_text().strip() == datetime.now(timezone.utc).strftime("%Y-%m-%d")
    stamp.write_text("2000-01-01\n")  # a new day notifies again
    assert preflight.main(["check"]) == 3
    assert len(sent) == 2


def test_idle_session_bookkeeping_is_not_activity(tmp_path):
    """An idle open session keeps rewriting its transcript (cost-state, last-prompt)
    without a new message: the file is fresh, but the owner is not active."""
    import json
    projects = tmp_path / "projects"
    (projects / "-p").mkdir(parents=True)
    f = projects / "-p" / "a.jsonl"
    f.write_text(_msg("user", "2026-10-27T21:00:00Z") + _msg("assistant", "2026-10-27T21:38:41Z")
                 + _msg("system", "2026-11-04T11:59:00Z")
                 + json.dumps({"type": "cost-state", "totalCostUSD": 0.8}) + "\n"
                 + json.dumps({"type": "last-prompt", "lastPrompt": "hi"}) + "\n")
    got = preflight.latest_owner_activity(projects, factory_home=Path("/zzz"))
    assert got == datetime(2026, 10, 27, 21, 38, 41, tzinfo=timezone.utc).timestamp()
    assert preflight.reasons_to_skip(NOW, CFG, statefile.defaults(), probes(latest_owner_activity=lambda: got)) == []


def test_newest_message_across_files_wins_even_in_an_older_file(tmp_path):
    projects = tmp_path / "projects"
    (projects / "-a").mkdir(parents=True)
    (projects / "-b").mkdir(parents=True)
    idle = projects / "-a" / "idle.jsonl"
    live = projects / "-b" / "live.jsonl"
    idle.write_text(_msg("user", "2026-10-01T00:00:00Z") + '{"type":"cost-state"}\n')
    live.write_text(_msg("assistant", "2026-11-04T11:55:00Z"))
    os.utime(live, (time.time() - 60, time.time() - 60))  # idle was written later
    got = preflight.latest_owner_activity(projects, factory_home=Path("/zzz"))
    assert got == datetime(2026, 11, 4, 11, 55, tzinfo=timezone.utc).timestamp()


def test_last_message_found_behind_a_long_tail(tmp_path):
    """The message sits further back than the first chunk read, and the line it is in straddles a chunk edge."""
    f = tmp_path / "t.jsonl"
    filler = '{"type":"api-request-blob","blob":"' + "x" * 50_000 + '"}\n'
    f.write_text(_msg("user", "2026-11-04T10:00:00Z", pad="y" * 70_000) + filler * 5)
    assert preflight.last_message_at(f) == datetime(2026, 11, 4, 10, 0, tzinfo=timezone.utc).timestamp()


def test_unreadable_lines_and_no_messages(tmp_path):
    f = tmp_path / "t.jsonl"
    f.write_text('not json\n{"type":"user","timestamp":"garbage"}\n{"type":"user","timestamp":"2026-11-04T10:00:00"}\n'
                 '{"type":"user"}\n')
    assert preflight.last_message_at(f) is None
    (tmp_path / "empty.jsonl").write_text("")
    assert preflight.last_message_at(tmp_path / "empty.jsonl") is None
    assert preflight.last_message_at(tmp_path / "missing.jsonl") is None
