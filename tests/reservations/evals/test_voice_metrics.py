"""Voice metrics, run meta and --compare. Pure: no database."""

import json
from pathlib import Path

import pytest

from backend.reservations.evals.checker import percentile
from backend.reservations.evals.compare import compare_docs, compare_main, load_run
from backend.reservations.evals.run import amain
from backend.reservations.evals.stats import (
    cost_per_booking,
    first_try_understanding, stage_percentiles, voice_section)
from backend.reservations.evals.store import RunMeta, write_run_file

FIX = Path(__file__).resolve().parents[2] / "fixtures" / "evals"


def _metrics(name: str) -> list[dict]:
    return [r["metrics"] for r in json.loads((FIX / f"{name}.json").read_text())["results"]]


def test_first_try_is_pooled_over_text_turns() -> None:
    # run_a: 6 text turns, 4 not repeated; action turns are not counted.
    m = _metrics("run_a")
    m[0]["voice"]["repeats"].append({"kind": "action", "repeat": True})
    assert first_try_understanding(m) == pytest.approx(4 / 6)


def test_first_try_na_without_voice() -> None:
    assert first_try_understanding(_metrics("run_old")) is None
    assert first_try_understanding([{"voice": {"repeats": []}}]) is None


def test_stage_percentiles_nearest_rank_and_missing_asr_tts() -> None:
    pp = stage_percentiles(_metrics("run_a"))
    assert pp["turn_total"] == (percentile([100, 200, 300, 400, 500, 600], 50),
                                percentile([100, 200, 300, 400, 500, 600], 95))
    assert pp["turn_total"] == (300, 600)
    assert pp["llm"] == (100, 100) and pp["tools"] == (10, 10)
    assert pp["asr"] is None and pp["tts"] is None
    m = _metrics("run_a")
    m[0]["voice"]["asr_ms"] = [50, 70]
    assert stage_percentiles(m)["asr"] == (50, 70)


def test_voice_section_lines() -> None:
    m = _metrics("run_a")
    text = "\n".join(voice_section(m, ["book", "cancel"]))
    assert "first-try understanding 66.7%" in text
    assert "bridge turn (excludes asr/tts) p50 300 ms, p95 600 ms" in text
    assert "recognizer errors 0" in text
    assert "repeats per scenario: book 1, cancel 1" in text
    assert "ssml errors 0" in text
    assert voice_section(_metrics("run_old"), ["book", "cancel"]) == []


def test_compare_two_fixture_runs() -> None:
    out = compare_docs(load_run(str(FIX / "run_a.json"), FIX),
                       load_run(str(FIX / "run_b.json"), FIX))
    assert "pass rate" in out and "50.0%" in out and "100.0%" in out
    assert "+50.0 pts" in out
    assert "first-try understanding" in out and "+33.3 pts" in out  # 66.7 -> 100
    assert "p50 bridge turn (excludes asr/tts)" in out and "p95 bridge turn" in out
    assert "recognizer: noisy" in out.split("\n")[0] or "recognizer" in out.split("scenario flips")[0]
    assert "$0.0200" in out and "$0.0400" in out  # book-goal cost / passing book-goal
    assert "cancel: fail -> pass" in out
    assert "book:" not in out.split("scenario flips:")[1]


def test_compare_mismatch_refused_unless_allowed(capsys) -> None:
    a, o = str(FIX / "run_a.json"), str(FIX / "run_other.json")
    assert compare_main(a, o, allow_mismatch=False, runs_dir=FIX) == 2
    assert "scenario sets differ" in capsys.readouterr().err
    assert compare_main(a, o, allow_mismatch=True, runs_dir=FIX) == 0


def test_compare_old_run_file_shows_na(capsys) -> None:
    assert compare_main(str(FIX / "run_old.json"), str(FIX / "run_b.json"),
                        allow_mismatch=False, runs_dir=FIX) == 0
    out = capsys.readouterr().out
    line = next(ln for ln in out.splitlines() if ln.startswith("first-try understanding ") and "only" not in ln)
    assert line.split()[-3:] == ["n/a", "100.0%", "n/a"]
    assert "n/a" in next(ln for ln in out.splitlines() if ln.startswith("p50 bridge turn"))


def test_compare_resolves_run_id_in_runs_dir(capsys) -> None:
    assert compare_main("run_a", "run_b", allow_mismatch=False, runs_dir=FIX) == 0


@pytest.mark.asyncio
async def test_cli_compare_needs_no_database(capsys) -> None:
    rc = await amain(["--compare", str(FIX / "run_a.json"), str(FIX / "run_b.json")],
                     database_url="postgresql+asyncpg://nobody@nowhere.invalid/x")
    assert rc == 0 and "pass rate" in capsys.readouterr().out


def test_run_meta_fields_written(tmp_path) -> None:
    from datetime import datetime, timezone

    from backend.reservations.evals.models import EvalRun

    run = EvalRun(id="run00002", git_sha="abc", agent_model="m", diner_model="d",
                  prompt_hash_web="w", prompt_hash_voice="v", mode="replay", repeat=1,
                  created_at=datetime(2026, 11, 18, tzinfo=timezone.utc))
    base = dict(git_sha="abc", agent_model="m", diner_model="d", prompt_hash_web="w",
                prompt_hash_voice="v", mode="replay", prompt_version="abcdef123456")
    doc = json.loads(write_run_file(run, [], RunMeta(
        **base, recognizer="noisy", seed=259),
        "r", tmp_path).read_text())
    assert doc["meta"]["recognizer"] == "noisy" and doc["meta"]["seed"] == 259
    assert doc["meta"]["voice_knobs"] == {} and "lex" not in doc["meta"]
    assert doc["meta"]["prompt_version"] == "abcdef123456"
    lex = {"bot_id": "B", "alias_id": "A", "locale": "en_US"}
    doc = json.loads(write_run_file(run, [], RunMeta(
        **base, recognizer="lex", lex=lex), "r", tmp_path).read_text())
    assert doc["meta"]["lex"] == lex


def test_cost_per_booking_single_definition() -> None:
    rs = [{"passed": True, "metrics": {"book_goal": True, "total_cost_usd": 0.02}},
          {"passed": False, "metrics": {"book_goal": True, "total_cost_usd": 0.04}},
          {"passed": True, "metrics": {"book_goal": False, "total_cost_usd": 9.0}}]
    assert cost_per_booking(rs) == pytest.approx(0.06)
    assert cost_per_booking(rs[1:2]) is None and cost_per_booking([]) is None

    from backend.reservations.evals.models import EvalResult
    from backend.reservations.evals.store import format_report
    from tests.reservations.evals.test_store import _run
    rows = [EvalResult(scenario_id="s", attempt=i, passed=r["passed"], failures=[],
                       metrics=r["metrics"], conversation_snapshot={})
            for i, r in enumerate(rs)]
    assert cost_per_booking(rows) == pytest.approx(0.06)
    assert "cost per booking $0.0600" in format_report(_run(), rows)


def test_load_run_id_stays_inside_runs_dir(tmp_path) -> None:
    (tmp_path / "x.json").write_text("{}")
    assert load_run("x", tmp_path) == {}
    with pytest.raises((OSError, ValueError)):
        load_run("..", tmp_path)
    with pytest.raises((OSError, ValueError)):
        load_run("nope", tmp_path)


def test_compare_malformed_file_exits_2(tmp_path, capsys) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"results": [{"passed": True}]}))
    assert compare_main(str(bad), str(bad), allow_mismatch=False, runs_dir=tmp_path) == 2
    bad.write_text(json.dumps({"results": [1]}))
    assert compare_main(str(bad), str(bad), allow_mismatch=False, runs_dir=tmp_path) == 2
    bad.write_text("[1]")
    assert compare_main(str(bad), str(bad), allow_mismatch=False, runs_dir=tmp_path) == 2


def test_compare_header_notes_voice_only(capsys) -> None:
    compare_main("run_a", "run_b", allow_mismatch=False, runs_dir=FIX)
    assert "voice attempts only" in capsys.readouterr().out


def test_percentile_edges() -> None:
    assert percentile([7], 50) == 7 and percentile([7], 95) == 7
    assert percentile([1, 2], 50) == 1 and percentile([1, 2], 95) == 2
    xs = list(range(1, 21))
    assert percentile(xs, 50) == 10 and percentile(xs, 95) == 19


def test_voice_section_counts_recognizer_errors() -> None:
    m = _metrics("run_a")
    m[0]["voice"]["errors"] = ["ThrottlingException", "ReadTimeoutError"]
    assert "recognizer errors 2" in "\n".join(voice_section(m, ["book", "cancel"]))


def test_compare_prints_meta_header_and_flags_differences() -> None:
    a = {"id": "a", "meta": {"mode": "live", "recognizer": "exact", "seed": 1,
                             "voice_knobs": {}, "prompt_version": "aaa"}, "results": []}
    b = {"id": "b", "meta": {"mode": "live", "recognizer": "noisy", "seed": 1,
                             "voice_knobs": {}, "prompt_version": "bbb"}, "results": []}
    lines = compare_docs(a, b).splitlines()
    flagged = [ln for ln in lines if ln.startswith("meta ") and "DIFFERS" in ln]
    assert any("recognizer" in ln for ln in flagged)
    assert any("prompt_version" in ln for ln in flagged)
    assert not any("seed" in ln for ln in flagged)
    assert any(ln.startswith("meta mode") and "DIFFERS" not in ln for ln in lines)
