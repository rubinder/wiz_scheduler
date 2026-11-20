"""scripts/run_agent_analytics (#259 step 10): dates, sinks, exit codes."""

from datetime import date
from pathlib import Path

import pytest

from backend.analytics import lake
from backend.config import settings
from backend.scripts import run_agent_analytics as script

TODAY = date(2026, 11, 19)


def test_default_is_yesterday_and_the_day_before() -> None:
    assert script.days_to_run(script.parse_args([]), TODAY) == [
        date(2026, 11, 17), date(2026, 11, 18)]


def test_date_processes_only_that_day() -> None:
    args = script.parse_args(["--date", "2026-11-01"])
    assert script.days_to_run(args, TODAY) == [date(2026, 11, 1)]


def test_backfill_iterates_the_dates() -> None:
    args = script.parse_args(["--backfill", "2026-10-30", "2026-11-02"])
    assert script.days_to_run(args, TODAY) == [
        date(2026, 10, 30), date(2026, 10, 31), date(2026, 11, 1), date(2026, 11, 2)]


def test_date_and_backfill_are_exclusive() -> None:
    with pytest.raises(SystemExit):
        script.parse_args(["--date", "2026-11-01", "--backfill", "2026-11-01", "2026-11-02"])


def test_local_uses_a_local_sink(tmp_path: Path) -> None:
    sink = script.make_sink(script.parse_args(["--local", str(tmp_path)]))
    assert isinstance(sink, lake.LocalSink) and sink.root == tmp_path


def test_s3_uses_the_bucket_and_prefix(monkeypatch) -> None:
    monkeypatch.setattr(settings, "ANALYTICS_BUCKET", "lake-bucket")
    import boto3

    client = object()
    monkeypatch.setattr(boto3, "client", lambda name: client if name == "s3" else None)
    sink = script.make_sink(script.parse_args([]))
    assert isinstance(sink, lake.S3Sink) and sink.client is client
    assert (sink.bucket, sink.prefix) == ("lake-bucket", "lake")


@pytest.mark.asyncio
async def test_no_bucket_and_no_local_exits_2(monkeypatch) -> None:
    monkeypatch.setattr(settings, "ANALYTICS_BUCKET", "")
    assert await script.main([]) == 2


@pytest.mark.asyncio
async def test_main_runs_each_day_and_exits_1_on_any_failure(monkeypatch,
                                                            tmp_path: Path) -> None:
    from backend.analytics import run

    seen: list[date] = []
    failed_alerts: list[tuple[str, str]] = []

    async def fake_run(day, sink, db):
        seen.append(day)
        if day == date(2026, 11, 2):
            raise RuntimeError("s3 down")
        return {"day": day.isoformat(), "ok": day != date(2026, 11, 1)}

    monkeypatch.setattr(run, "run", fake_run)
    monkeypatch.setattr("backend.services.operator_alerts.send_analytics_run_failed_alert",
                        lambda day, err: failed_alerts.append((day, err)) or True)
    status = await script.main(["--backfill", "2026-10-31", "2026-11-02",
                                "--local", str(tmp_path)])
    assert status == 1
    assert seen == [date(2026, 10, 31), date(2026, 11, 1), date(2026, 11, 2)]
    assert failed_alerts == [("2026-11-02", "RuntimeError")]


@pytest.mark.asyncio
async def test_main_exits_0_when_every_day_passes(monkeypatch, tmp_path: Path) -> None:
    from backend.analytics import run

    async def fake_run(day, sink, db):
        return {"day": day.isoformat(), "ok": True}

    monkeypatch.setattr(run, "run", fake_run)
    assert await script.main(["--date", "2026-11-01", "--local", str(tmp_path)]) == 0
