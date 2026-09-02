"""Weekly suspected-account report.

Run manually or via cron:
    python -m backend.scripts.run_abuse_report

Suggested cron (Mondays at 4 AM, after the nightly retention purge):
    0 4 * * 1 cd /app && python -m backend.scripts.run_abuse_report

Prints the report as JSON and logs a one-line summary. It writes nothing
and deletes nothing — the output is for a human to read. See
backend/services/abuse_report.py for what the signals do and do not mean.
"""
import argparse
import asyncio
import json
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def main(window_days: int) -> None:
    from backend.database import async_session_factory
    from backend.services.abuse_report import build_suspected_accounts_report

    async with async_session_factory() as db:
        report = await build_suspected_accounts_report(
            db, window_days=window_days
        )
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--window-days",
        type=int,
        default=90,
        help=(
            "How far back to look. Signals are deleted after "
            "RETENTION_SIGNUP_SIGNALS_DAYS, so a wider window finds no more."
        ),
    )
    args = parser.parse_args()
    asyncio.run(main(args.window_days))
