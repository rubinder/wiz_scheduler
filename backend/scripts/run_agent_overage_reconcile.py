"""Daily AI host billing run (#259 step 5).

For every ownership group with an agent subscription that has not ended:
re-sync it from Stripe (catches a missed customer.subscription.deleted),
then bill its current period's usage over the included amount as an
InvoiceItem on its next invoice, and true up the previous period. See
backend/services/billing_reservations.reconcile_all_agent_subscriptions.

Run manually:
    python -m backend.scripts.run_agent_overage_reconcile

The production schedule (a daily one-off ECS task) is part of the
reservations infra change; the owner applies it.
"""
import asyncio
import html
import json
import logging

logging.basicConfig(level=logging.INFO)


async def main() -> None:
    from backend.database import async_session_factory
    from backend.services.billing_reservations import reconcile_all_agent_subscriptions
    from backend.services.operator_alerts import _send_operator_email

    async with async_session_factory() as db:
        summary = await reconcile_all_agent_subscriptions(db)
    print(json.dumps(summary, indent=2, default=str))
    if summary["errors"]:
        items = "".join(f"<li>{html.escape(json.dumps(e, default=str))}</li>"
                        for e in summary["errors"])
        _send_operator_email(
            "[WizScheduler] AI host billing run needs a look",
            f"<p>The daily AI host billing run could not finish these groups:</p><ul>{items}</ul>",
            "agent_overage_reconcile",
        )


if __name__ == "__main__":
    asyncio.run(main())
