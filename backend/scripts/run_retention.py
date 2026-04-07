"""Data retention purge script.

Run manually or via cron:
    python -m backend.scripts.run_retention

Suggested cron (daily at 3 AM):
    0 3 * * * cd /app && python -m backend.scripts.run_retention
"""
import asyncio
import json
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def main() -> None:
    from backend.database import async_session_factory
    from backend.services.data_retention import run_data_retention

    async with async_session_factory() as db:
        summary = await run_data_retention(db)
        print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
