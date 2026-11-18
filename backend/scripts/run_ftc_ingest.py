"""Weekly FTC Do Not Call reported-numbers ingest (#259 step 7).

Fetches the FTC's weekday complaint files not yet ingested and loads them
into spam_numbers for call screening; see backend/reservations/ftc_ingest.
Mails the operator when the run fails, a file fails or holds no valid
number, or nothing has been ingested for FTC_INGEST_STALE_DAYS.

The session is a plain async_session_factory() session: no RLS binding, so
it runs as the table owner, the only role that may write the spam tables
(migration 0063).

Always exits 0, also after an alert, so the scheduled ECS task never
retry-loops; the next weekly run picks up from the newest OK file.

Run manually:
    python -m backend.scripts.run_ftc_ingest

Scheduled weekly (Terraform ftc_ingest_schedule) when ftc_ingest_enabled.
"""
import asyncio
import json
import logging
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("run_ftc_ingest")


async def main() -> None:
    from backend.database import async_session_factory
    from backend.reservations.ftc_ingest import run_job

    async with async_session_factory() as db:
        summary = await run_job(db, datetime.now(timezone.utc).date())
    # Dates and counts only: the summary never carries a phone number.
    logger.info("FTC ingest summary: %s", json.dumps(summary, default=str))


if __name__ == "__main__":
    asyncio.run(main())
