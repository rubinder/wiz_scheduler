"""The weekly FTC Do Not Call reported-numbers ingest (#259 step 7)."""

import io
import urllib.error
from datetime import date, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.reservations import ftc_ingest
from backend.reservations.ftc_ingest import _record, alert_reasons, ingest, ingest_file, parse, parse_counted
from backend.reservations.screening_models import SpamIngestFile, SpamNumber

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).parent.parent / "fixtures" / "ftc"
TODAY = date(2026, 11, 18)  # a Wednesday


def _csv(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _url(d: date) -> str:
    return settings.FTC_DNC_CSV_URL.format(date=d.isoformat())


class FakeFetch:
    """url -> bytes; a missing url is a 404 (None); an Exception is raised."""

    def __init__(self, files: dict[date, bytes | Exception]) -> None:
        self.files = {_url(d): v for d, v in files.items()}
        self.calls: list[str] = []

    def __call__(self, url: str) -> bytes | None:
        self.calls.append(url)
        value = self.files.get(url)
        if isinstance(value, Exception):
            raise value
        return value


async def _files(db: AsyncSession) -> dict[date, tuple[str, int]]:
    rows = (await db.execute(select(SpamIngestFile))).scalars().all()
    return {r.file_date: (r.status, r.rows) for r in rows}


async def _reports(db: AsyncSession, e164: str) -> int:
    return (await db.execute(
        select(func.coalesce(func.sum(SpamNumber.reports), 0))
        .where(SpamNumber.e164 == e164))).scalar_one()


# --- parse ------------------------------------------------------------------

def test_parse_counts_numbers_and_skips_junk():
    counts, invalid = parse_counted(_csv("standard.csv"))
    assert counts == {"+12125550142": 2, "+16465550199": 1, "+19175550100": 1}
    assert invalid == 4  # blank, 7 digits, 12 digits, words
    assert parse(_csv("standard.csv")) == counts


def test_parse_prefers_company_phone_number_case_insensitively():
    assert parse(_csv("both_phone_columns.csv")) == {
        "+12125550142": 1, "+16465550199": 1}


def test_parse_falls_back_to_a_header_containing_phone():
    assert parse(_csv("phone_variant.csv")) == {"+12125550142": 2}


def test_parse_without_a_phone_column_fails():
    with pytest.raises(ValueError):
        parse(_csv("no_phone_column.csv"))


def test_parse_survives_undecodable_bytes():
    assert parse(b"Company_Phone_Number\n\xff\xfe2125550142\n") == {"+12125550142": 1}


# --- fetch ------------------------------------------------------------------

def test_read_capped_stops_past_the_cap():
    assert ftc_ingest._read_capped(io.BytesIO(b"x" * 10), 10) == b"x" * 10
    with pytest.raises(ftc_ingest.TooLarge):
        ftc_ingest._read_capped(io.BytesIO(b"x" * 11), 10)


def test_default_fetch_treats_404_as_not_published(monkeypatch):
    def urlopen(req, timeout):
        assert req.get_header("User-agent") == "WizScheduler-FTC-ingest/1.0"
        assert timeout == settings.FTC_INGEST_TIMEOUT_SECONDS
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", None, None)

    monkeypatch.setattr(ftc_ingest.urllib.request, "urlopen", urlopen)
    assert ftc_ingest.default_fetch("https://example.test/x.csv") is None


def test_default_fetch_raises_other_http_errors(monkeypatch):
    def urlopen(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 500, "Oops", None, None)

    monkeypatch.setattr(ftc_ingest.urllib.request, "urlopen", urlopen)
    with pytest.raises(urllib.error.HTTPError):
        ftc_ingest.default_fetch("https://example.test/x.csv")


# --- ingest -----------------------------------------------------------------

@pytest.mark.asyncio
async def test_first_run_fetches_weekdays_from_two_weeks_back(db_session: AsyncSession):
    holiday = date(2026, 11, 11)
    files = {d: _csv("standard.csv") for d in
             (date(2026, 11, 4) + timedelta(n) for n in range(15))
             if d.weekday() < 5 and d not in (holiday, TODAY)}
    fetch = FakeFetch(files)

    summary = await ingest(db_session, TODAY, fetch=fetch)

    expected = [d for d in (date(2026, 11, 4) + timedelta(n) for n in range(15))
                if d.weekday() < 5]
    assert fetch.calls == [_url(d) for d in expected]
    recorded = await _files(db_session)
    assert recorded[holiday] == ("missing", 0)
    assert recorded[TODAY] == ("missing", 0)  # not published until noon Eastern
    assert recorded[date(2026, 11, 17)] == ("ok", 3)
    assert summary["ok"] == 9 and summary["missing"] == 2
    assert summary["errors"] == [] and summary["empty"] == []
    assert summary["last_ok"] == "2026-11-17"
    assert summary["stale"] is False
    assert await _reports(db_session, "+12125550142") == 2 * 9


@pytest.mark.asyncio
async def test_no_read_transaction_is_held_through_the_first_fetch(
        db_session: AsyncSession):
    seen: list[bool] = []

    def fetch(url: str) -> bytes | None:
        seen.append(db_session.in_transaction())
        return None
    await ingest(db_session, TODAY, fetch=fetch)
    assert seen and seen[0] is False


@pytest.mark.asyncio
async def test_later_run_fetches_only_dates_not_yet_ok(db_session: AsyncSession):
    db_session.add_all([SpamIngestFile(file_date=date(2026, 11, 13), status="ok", rows=1),
                        SpamIngestFile(file_date=date(2026, 11, 16), status="error")])
    await db_session.commit()
    fetch = FakeFetch({date(2026, 11, 16): _csv("standard.csv")})

    await ingest(db_session, TODAY, fetch=fetch)

    assert fetch.calls == [_url(date(2026, 11, d)) for d in (16, 17, 18)]
    assert (await _files(db_session))[date(2026, 11, 16)] == ("ok", 3)


@pytest.mark.asyncio
async def test_a_failed_date_is_retried_after_a_later_ok(db_session: AsyncSession):
    tue, wed = date(2026, 11, 17), TODAY
    db_session.add_all([SpamIngestFile(file_date=date(2026, 11, 16), status="ok", rows=1),
                        SpamIngestFile(file_date=tue, status="error"),
                        SpamIngestFile(file_date=wed, status="ok", rows=1)])
    await db_session.commit()
    fetch = FakeFetch({tue: _csv("standard.csv")})

    await ingest(db_session, TODAY, fetch=fetch)

    assert fetch.calls == [_url(tue)]
    assert (await _files(db_session))[tue] == ("ok", 3)


@pytest.mark.asyncio
async def test_empty_dates_are_retried_and_old_missing_ones_are_not(db_session: AsyncSession):
    old_missing = date(2026, 11, 6)    # 12 days back: a holiday, give up
    recent_missing = date(2026, 11, 9)  # 9 days back: maybe late, ask again
    empty = date(2026, 11, 10)
    db_session.add_all([
        SpamIngestFile(file_date=date(2026, 11, 5), status="ok", rows=1),
        SpamIngestFile(file_date=old_missing, status="missing"),
        SpamIngestFile(file_date=recent_missing, status="missing"),
        SpamIngestFile(file_date=empty, status="empty"),
    ] + [SpamIngestFile(file_date=date(2026, 11, d), status="ok", rows=1)
         for d in (11, 12, 13, 16, 17, 18)])
    await db_session.commit()
    fetch = FakeFetch({})

    await ingest(db_session, TODAY, fetch=fetch)

    assert fetch.calls == [_url(recent_missing), _url(empty)]


@pytest.mark.asyncio
async def test_record_never_downgrades_an_ok_file(db_session: AsyncSession):
    d = date(2026, 11, 17)
    await ingest_file(db_session, d, _csv("standard.csv"))
    for status in ("error", "missing", "empty"):
        await _record(db_session, d, status, 0)
        await db_session.commit()
    assert (await _files(db_session))[d] == ("ok", 3)
    await _record(db_session, d, "ok", 7)
    await db_session.commit()
    assert (await _files(db_session))[d] == ("ok", 7)


@pytest.mark.asyncio
async def test_a_failure_to_record_an_error_does_not_stop_the_run(
        db_session: AsyncSession, monkeypatch):
    real = ftc_ingest._record

    async def flaky(db, file_date, status, rows):
        if status == "error":
            raise RuntimeError("db hiccup")
        await real(db, file_date, status, rows)

    monkeypatch.setattr(ftc_ingest, "_record", flaky)
    db_session.add(SpamIngestFile(file_date=date(2026, 11, 13), status="ok", rows=1))
    await db_session.commit()
    fetch = FakeFetch({date(2026, 11, 16): RuntimeError("boom"),
                       date(2026, 11, 17): _csv("standard.csv")})

    summary = await ingest(db_session, TODAY, fetch=fetch)

    assert [e["date"] for e in summary["errors"]] == ["2026-11-16"]
    assert (await _files(db_session))[date(2026, 11, 17)] == ("ok", 3)


@pytest.mark.asyncio
async def test_reprocessing_a_file_replaces_its_counts(db_session: AsyncSession):
    d = date(2026, 11, 17)
    await ingest_file(db_session, d, _csv("standard.csv"))
    await ingest_file(db_session, d, _csv("standard.csv"))
    assert await _reports(db_session, "+12125550142") == 2
    await ingest_file(db_session, d, _csv("phone_variant.csv"))
    assert await _reports(db_session, "+12125550142") == 2
    assert await _reports(db_session, "+16465550199") == 0


@pytest.mark.asyncio
async def test_rerun_on_the_same_day_does_not_double_count(db_session: AsyncSession):
    fetch = FakeFetch({TODAY: _csv("standard.csv"), date(2026, 11, 17): _csv("standard.csv")})
    await ingest(db_session, TODAY, fetch=fetch)
    # Simulate a crash before the next file was recorded: re-run from scratch.
    await db_session.execute(SpamIngestFile.__table__.delete())
    await db_session.commit()
    await ingest(db_session, TODAY, fetch=fetch)
    assert await _reports(db_session, "+12125550142") == 4


@pytest.mark.asyncio
async def test_fetch_and_parse_failures_are_recorded_per_file(db_session: AsyncSession):
    fetch = FakeFetch({
        date(2026, 11, 16): RuntimeError("boom"),
        date(2026, 11, 17): _csv("no_phone_column.csv"),
        TODAY: _csv("no_valid_rows.csv"),
        date(2026, 11, 13): _csv("standard.csv"),
    })
    db_session.add(SpamIngestFile(file_date=date(2026, 11, 12), status="ok", rows=1))
    await db_session.commit()

    summary = await ingest(db_session, TODAY, fetch=fetch)

    recorded = await _files(db_session)
    assert recorded[date(2026, 11, 13)] == ("ok", 3)
    assert recorded[date(2026, 11, 16)][0] == "error"
    assert recorded[date(2026, 11, 17)][0] == "error"
    assert recorded[TODAY] == ("empty", 0)
    assert [e["date"] for e in summary["errors"]] == ["2026-11-16", "2026-11-17"]
    assert summary["empty"] == ["2026-11-18"]
    assert "2125550142" not in repr(summary)


@pytest.mark.asyncio
async def test_ingest_prunes_rows_older_than_the_window(db_session: AsyncSession):
    edge = TODAY - timedelta(days=settings.SPAM_WINDOW_DAYS)
    db_session.add_all([
        SpamNumber(e164="+12125550101", report_date=edge - timedelta(days=1), reports=5),
        SpamNumber(e164="+12125550102", report_date=edge, reports=5),
        SpamIngestFile(file_date=TODAY, status="ok", rows=1),
    ])
    await db_session.commit()

    summary = await ingest(db_session, TODAY, fetch=FakeFetch({}))

    assert summary["pruned"] == 1
    assert await _reports(db_session, "+12125550101") == 0
    assert await _reports(db_session, "+12125550102") == 5


@pytest.mark.asyncio
async def test_stale_when_no_ok_file_for_eight_days(db_session: AsyncSession):
    last = TODAY - timedelta(days=settings.FTC_INGEST_STALE_DAYS)
    db_session.add(SpamIngestFile(file_date=last, status="ok", rows=1))
    await db_session.commit()
    summary = await ingest(db_session, TODAY, fetch=FakeFetch({}))
    assert summary["stale"] is True
    assert alert_reasons(summary)


# --- alerts -----------------------------------------------------------------

def _summary(**over) -> dict:
    base = {"stale": False, "empty": [], "errors": []}
    return base | over


def test_alert_reasons():
    assert alert_reasons(_summary()) == []
    assert alert_reasons(_summary(stale=True))
    assert alert_reasons(_summary(empty=["2026-11-17"]))
    assert alert_reasons(_summary(errors=[{"date": "2026-11-17", "error": "x"}]))


@pytest.mark.asyncio
async def test_run_job_alerts_when_the_run_raises(db_session: AsyncSession, monkeypatch):
    sent: list[dict] = []
    monkeypatch.setattr(ftc_ingest, "send_ftc_ingest_alert", lambda s: sent.append(s) or True)

    async def explode(db, today, fetch):
        raise RuntimeError("db down")

    monkeypatch.setattr(ftc_ingest, "ingest", explode)
    summary = await ftc_ingest.run_job(db_session, TODAY, fetch=FakeFetch({}))
    assert summary["failed"] == "RuntimeError"
    assert len(sent) == 1


@pytest.mark.asyncio
async def test_run_job_alerts_only_when_needed(db_session: AsyncSession, monkeypatch):
    sent: list[dict] = []
    monkeypatch.setattr(ftc_ingest, "send_ftc_ingest_alert", lambda s: sent.append(s) or True)
    await ftc_ingest.run_job(db_session, TODAY, fetch=FakeFetch({TODAY: _csv("standard.csv")}))
    assert sent == []
    await ftc_ingest.run_job(db_session, TODAY + timedelta(days=1),
                             fetch=FakeFetch({TODAY + timedelta(days=1): _csv("no_valid_rows.csv")}))
    assert len(sent) == 1
