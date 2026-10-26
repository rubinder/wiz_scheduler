"""Data retention purge script.

Run manually:
    python -m backend.scripts.run_retention

Production runs it monthly (03:00 UTC on the 1st) as a one-off ECS task;
see terraform/retention_schedule.tf.
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
