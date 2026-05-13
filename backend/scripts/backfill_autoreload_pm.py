"""One-shot migration: cache default_payment_method_id for every OG with a subscription.

Run after deploying the auto-reload billing release to populate the new column
for customers who already have an active Stripe subscription. New signups will
populate it automatically via the auto-reload flow.

Usage (locally):
    python -m backend.scripts.backfill_autoreload_pm

Usage (against ECS, one-off task):
    aws ecs run-task --cluster wizscheduler-cluster \\
      --task-definition wizscheduler --launch-type FARGATE \\
      --network-configuration "awsvpcConfiguration={subnets=[...],securityGroups=[...],assignPublicIp=DISABLED}" \\
      --overrides '{"containerOverrides":[{"name":"wizscheduler",
        "command":["sh","-c","cd /app && python -m backend.scripts.backfill_autoreload_pm"]}]}'

Idempotent — re-running is safe (refreshes the cached PM if Stripe returns a new one).
"""
import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.config import settings
from backend.models.ownership_group import OwnershipGroup
from backend.services.billing import cache_default_payment_method

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


async def main() -> None:
    engine = create_async_engine(settings.DATABASE_URL)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async with Session() as db:
        result = await db.execute(
            select(OwnershipGroup).where(OwnershipGroup.stripe_subscription_id.is_not(None))
        )
        ogs = list(result.scalars())
        logger.info("Found %d OG(s) with a subscription", len(ogs))

        for og in ogs:
            try:
                pm_id = await cache_default_payment_method(db, og)
                logger.info("  %s (%s): default_pm=%s", og.id, og.name, pm_id or "<none>")
            except Exception as e:
                logger.error("  %s (%s): FAILED %s", og.id, og.name, e)

        await db.commit()
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
