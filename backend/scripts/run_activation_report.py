"""Weekly activation-funnel cohort report.

Run manually or via cron:
    python -m backend.scripts.run_activation_report

Suggested cron (Mondays at 5 AM, after run_abuse_report):
    0 5 * * 1 cd /app && python -m backend.scripts.run_activation_report

Prints the report as JSON and logs a one-line summary. It writes nothing —
the output is for a human to read. See backend/services/activation_report.py
for what the cohort math does and does not mean, and backend/services/
activation.py for how the underlying rows are written.
"""
import argparse
import asyncio
import json
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def main(weeks: int) -> None:
    from backend.database import async_session_factory
    from backend.services.activation_report import build_activation_report

    async with async_session_factory() as db:
        report = await build_activation_report(db, weeks=weeks)
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--weeks",
        type=int,
        default=8,
        help="How many weekly signup cohorts (most recent first) to report on.",
    )
    args = parser.parse_args()
    asyncio.run(main(args.weeks))
