"""The weekly FTC Do Not Call reported-numbers ingest (#259 step 7).

The FTC publishes one CSV per weekday of numbers consumers reported that
day. This job fetches each weekday file from the day after the newest file
it ingested OK (two weeks back on the first run) through today, and
replaces that date's spam_numbers rows with the file's per-number counts,
so re-running a file never double-counts. Rows older than
SPAM_WINDOW_DAYS are pruned. reservations.screening reads the result.

Writes: spam_numbers and spam_ingest_files are read-only to app_role
(migration 0063). The session passed in must carry no RLS binding, so it
runs as the table owner: the script opens a plain async_session_factory()
session, like the other scheduled scripts. Never call this from a request.

Never logs or returns a phone number: the summary carries dates and counts.
"""

import asyncio
import csv
import io
import logging
import re
import urllib.error
import urllib.request
from collections import Counter
from collections.abc import Callable
from datetime import date, timedelta
from typing import Any, BinaryIO

from sqlalchemy import delete, func, insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.reservations.screening_models import SpamIngestFile, SpamNumber
from backend.services.operator_alerts import send_ftc_ingest_alert

logger = logging.getLogger(__name__)

USER_AGENT = "WizScheduler-FTC-ingest/1.0"
FIRST_RUN_DAYS = 14
INSERT_BATCH = 5000
_CHUNK = 64 * 1024
_NON_DIGITS = re.compile(r"\D")

Fetch = Callable[[str], bytes | None]


class TooLarge(Exception):
    """A source file exceeded FTC_INGEST_MAX_BYTES."""


# --- parse ------------------------------------------------------------------

def _phone_column(header: list[str]) -> int:
    names = [h.strip().lower() for h in header]
    if "company_phone_number" in names:
        return names.index("company_phone_number")
    for i, name in enumerate(names):
        if "phone" in name:
            return i
    raise ValueError("no phone column in the header")


def _e164(cell: str) -> str | None:
    digits = _NON_DIGITS.sub("", cell)
    if len(digits) == 10:
        return "+1" + digits
    if len(digits) == 11 and digits.startswith("1"):
        return "+" + digits
    return None


def parse_counted(csv_bytes: bytes) -> tuple[dict[str, int], int]:
    """(e164 -> reports in this file, rows skipped as invalid). Raises
    ValueError when the file has no header or no phone column."""
    reader = csv.reader(io.StringIO(csv_bytes.decode("utf-8-sig", errors="replace")))
    header = next(reader, None)
    if not header:
        raise ValueError("empty file")
    col = _phone_column(header)
    counts: Counter[str] = Counter()
    invalid = 0
    for row in reader:
        if not row:
            continue
        number = _e164(row[col]) if col < len(row) else None
        if number is None:
            invalid += 1
        else:
            counts[number] += 1
    return dict(counts), invalid


def parse(csv_bytes: bytes) -> dict[str, int]:
    """e164 -> report count for one FTC file. Invalid rows are skipped."""
    return parse_counted(csv_bytes)[0]


# --- fetch ------------------------------------------------------------------

def _read_capped(stream: BinaryIO, max_bytes: int) -> bytes:
    buf = bytearray()
    while chunk := stream.read(_CHUNK):
        buf += chunk
        if len(buf) > max_bytes:
            raise TooLarge(f"over {max_bytes} bytes")
    return bytes(buf)


def default_fetch(url: str) -> bytes | None:
    """The file's bytes, or None on 404 (not published: today before noon
    Eastern, a holiday). Raises on any other failure. Blocking."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=settings.FTC_INGEST_TIMEOUT_SECONDS) as resp:
            return _read_capped(resp, settings.FTC_INGEST_MAX_BYTES)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


# --- ingest -----------------------------------------------------------------

async def _record(db: AsyncSession, file_date: date, status: str, rows: int) -> None:
    stmt = pg_insert(SpamIngestFile).values(file_date=file_date, status=status, rows=rows)
    await db.execute(stmt.on_conflict_do_update(
        index_elements=[SpamIngestFile.file_date],
        set_={"status": status, "rows": rows, "fetched_at": func.now()}))


async def ingest_file(db: AsyncSession, file_date: date, data: bytes) -> tuple[str, int, int]:
    """Replace file_date's rows with the file's counts and record it, in one
    transaction. Returns (status, distinct numbers, invalid rows). A file with
    no valid number is recorded "empty" and leaves existing rows alone.
    Raises ValueError (nothing written) when the file cannot be parsed."""
    counts, invalid = parse_counted(data)
    if not counts:
        await _record(db, file_date, "empty", 0)
        await db.commit()
        return "empty", 0, invalid
    await db.execute(delete(SpamNumber).where(SpamNumber.report_date == file_date))
    items = list(counts.items())
    for i in range(0, len(items), INSERT_BATCH):
        await db.execute(insert(SpamNumber), [
            {"e164": e164, "report_date": file_date, "reports": n}
            for e164, n in items[i:i + INSERT_BATCH]])
    await _record(db, file_date, "ok", len(counts))
    await db.commit()
    return "ok", len(counts), invalid


async def _last_ok(db: AsyncSession) -> date | None:
    return (await db.execute(
        select(func.max(SpamIngestFile.file_date)).where(SpamIngestFile.status == "ok")
    )).scalar_one()


def _weekdays(start: date, end: date) -> list[date]:
    days = (end - start).days + 1
    return [d for d in (start + timedelta(n) for n in range(max(days, 0))) if d.weekday() < 5]


async def ingest(db: AsyncSession, today: date, fetch: Fetch = default_fetch) -> dict[str, Any]:
    """Fetch and load every weekday file not yet ingested OK, through today,
    then prune. One failed file is recorded and skipped, never fatal."""
    last_ok = await _last_ok(db)
    start = last_ok + timedelta(days=1) if last_ok else today - timedelta(days=FIRST_RUN_DAYS)
    # A file older than the window would be pruned straight away.
    start = max(start, today - timedelta(days=settings.SPAM_WINDOW_DAYS))
    dates = _weekdays(start, today)

    summary: dict[str, Any] = {
        "today": today.isoformat(), "from": start.isoformat(), "files": len(dates),
        "ok": 0, "missing": 0, "empty": [], "errors": [], "numbers": 0, "invalid": 0,
    }
    for d in dates:
        url = settings.FTC_DNC_CSV_URL.format(date=d.isoformat())
        try:
            data = await asyncio.to_thread(fetch, url)
            if data is None:
                await _record(db, d, "missing", 0)
                await db.commit()
                summary["missing"] += 1
                continue
            status, numbers, invalid = await ingest_file(db, d, data)
        except Exception as exc:  # one bad file must not stop the rest
            await db.rollback()
            logger.warning("[FTC] %s failed: %s", d.isoformat(), type(exc).__name__)
            await _record(db, d, "error", 0)
            await db.commit()
            summary["errors"].append({"date": d.isoformat(), "error": type(exc).__name__})
            continue
        summary["invalid"] += invalid
        if status == "ok":
            summary["ok"] += 1
            summary["numbers"] += numbers
        else:
            summary["empty"].append(d.isoformat())

    cutoff = today - timedelta(days=settings.SPAM_WINDOW_DAYS)
    pruned = await db.execute(delete(SpamNumber).where(SpamNumber.report_date < cutoff))
    await db.commit()
    summary["pruned"] = pruned.rowcount

    newest = await _last_ok(db)
    summary["last_ok"] = newest.isoformat() if newest else None
    summary["stale"] = newest is None or (today - newest).days >= settings.FTC_INGEST_STALE_DAYS
    return summary


def alert_reasons(summary: dict[str, Any]) -> list[str]:
    """Why the operator should look at this run; empty when all is well."""
    reasons: list[str] = []
    if summary.get("failed"):
        reasons.append(f"the run raised {summary['failed']}")
    if summary.get("stale"):
        reasons.append(f"no file ingested OK for {settings.FTC_INGEST_STALE_DAYS}+ days "
                       f"(newest: {summary.get('last_ok') or 'none'})")
    if summary.get("empty"):
        reasons.append("files with no valid number: " + ", ".join(summary["empty"]))
    if summary.get("errors"):
        reasons.append("files that failed: " + ", ".join(
            f"{e['date']} ({e['error']})" for e in summary["errors"]))
    return reasons


async def run_job(db: AsyncSession, today: date, fetch: Fetch = default_fetch) -> dict[str, Any]:
    """ingest, then alert the operator if needed. Never raises."""
    try:
        summary = await ingest(db, today, fetch=fetch)
    except Exception as exc:
        logger.exception("[FTC] ingest run failed")
        summary = {"today": today.isoformat(), "failed": type(exc).__name__}
    reasons = alert_reasons(summary)
    if reasons:
        send_ftc_ingest_alert(summary | {"reasons": reasons})
    return summary
