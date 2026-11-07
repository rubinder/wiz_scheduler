"""Daily Stripe reconciliation.

Run manually:
    python -m backend.scripts.run_stripe_reconcile

Production runs it daily (04:00 UTC) as a one-off ECS task; see
terraform/stripe_reconcile_schedule.tf and backend/services/stripe_reconcile.py.
"""
import asyncio
import json
import logging

logging.basicConfig(level=logging.INFO)


async def main() -> None:
    from backend.database import async_session_factory
    from backend.services.stripe_reconcile import run_stripe_reconcile

    async with async_session_factory() as db:
        summary = await run_stripe_reconcile(db)
        print(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
