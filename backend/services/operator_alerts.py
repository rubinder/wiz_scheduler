"""Emails to the site operator, not to customers.

Nothing here counts against a tenant's email cap (services/email_quota):
the operator is the recipient, and the mail exists so they can act on
their own Anthropic Console, which has no API for buying credits.
"""
from __future__ import annotations

import html
import logging

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models.ownership_group import OwnershipGroup

logger = logging.getLogger(__name__)


async def _total_prepaid_usd(db: AsyncSession) -> float:
    total = (await db.execute(
        select(func.coalesce(func.sum(OwnershipGroup.ai_credits_usd), 0.0))
    )).scalar_one()
    return float(total)


async def send_credit_purchase_alert(
    db: AsyncSession,
    og: OwnershipGroup,
    amount_usd: float,
    kind: str,
) -> bool:
    """Tell the operator a customer paid for credits and what to buy upstream.

    Customers pay LLM_OVERAGE_MARKUP × token cost, so amount / markup is the
    Anthropic-side spend this charge funds. The total across all groups is
    the outstanding liability to compare against the Console balance.

    Always writes one [OPERATOR] log line. Sends email only when both
    OPERATOR_ALERT_EMAIL and RESEND_API_KEY are set. Never raises: the
    charge has already happened and must not be undone by a mail failure.

    Returns True only when Resend accepted the send.
    """
    markup = settings.LLM_OVERAGE_MARKUP
    anthropic_equiv = round(amount_usd / markup, 2)
    try:
        total_prepaid = await _total_prepaid_usd(db)
    except Exception:
        logger.exception("[OPERATOR] could not sum prepaid balances")
        total_prepaid = 0.0
    total_equiv = round(total_prepaid / markup, 2)

    logger.info(
        "[OPERATOR] credit_charge og=%s kind=%s paid=%.2f anthropic_equiv=%.2f "
        "total_prepaid=%.2f total_anthropic_equiv=%.2f",
        og.id, kind, amount_usd, anthropic_equiv, total_prepaid, total_equiv,
    )

    if not settings.OPERATOR_ALERT_EMAIL or not settings.RESEND_API_KEY:
        return False

    label = "purchase" if kind == "purchase" else "auto-reload"
    subject = (
        f"[WizScheduler] AI credit {label} ${amount_usd:.2f} — "
        f"top up Anthropic by ${anthropic_equiv:.2f}"
    )
    body = (
        '<div style="font-family:sans-serif;max-width:600px;margin:0 auto;">'
        f"<p><strong>{html.escape(og.name)}</strong> (og={html.escape(og.id)}) "
        f"paid <strong>${amount_usd:.2f}</strong> for AI credits ({label}).</p>"
        f"<p>At {markup:.0%} markup that funds <strong>${anthropic_equiv:.2f}</strong> "
        f"of Anthropic usage. Top up the Console by at least that.</p>"
        f"<p>Outstanding prepaid across all accounts: ${total_prepaid:.2f}, "
        f"which is ${total_equiv:.2f} at Anthropic. Keep the Console balance "
        f"above that figure.</p>"
        "</div>"
    )

    try:
        import resend

        resend.api_key = settings.RESEND_API_KEY
        resend.Emails.send({
            "from": settings.FROM_EMAIL,
            "to": [settings.OPERATOR_ALERT_EMAIL],
            "subject": subject,
            "html": body,
        })
        return True
    except Exception:
        logger.exception("[OPERATOR] credit_charge email failed og=%s kind=%s", og.id, kind)
        return False


def _send_operator_email(subject: str, body_html: str, log_label: str) -> bool:
    """Mail the operator if both OPERATOR_ALERT_EMAIL and RESEND_API_KEY are
    set. Never raises. True only when Resend accepted the send."""
    if not settings.OPERATOR_ALERT_EMAIL or not settings.RESEND_API_KEY:
        return False
    try:
        import resend

        resend.api_key = settings.RESEND_API_KEY
        resend.Emails.send({
            "from": settings.FROM_EMAIL,
            "to": [settings.OPERATOR_ALERT_EMAIL],
            "subject": subject,
            "html": (
                '<div style="font-family:sans-serif;max-width:600px;margin:0 auto;">'
                f"{body_html}</div>"
            ),
        })
        return True
    except Exception:
        logger.exception("[OPERATOR] %s email failed", log_label)
        return False


def send_payroll_archive_write_failed_alert(
    og_id: str, archive_id: str, error: str
) -> bool:
    """A paid payroll archive (#218) could not be written at day 90.

    The group was NOT deleted; the daily cron retries. Someone should look
    before the next run (an unset PAYROLL_ARCHIVE_BUCKET, an IAM change, an
    S3 outage). Always logs one [OPERATOR] line. Never raises.
    """
    logger.error(
        "[OPERATOR] payroll_archive_write_failed og=%s archive=%s error=%s",
        og_id, archive_id, error,
    )
    return _send_operator_email(
        f"[WizScheduler] Payroll archive write failed for og={og_id}",
        f"<p>The paid payroll archive <strong>{html.escape(archive_id)}</strong> "
        f"for og={html.escape(og_id)} could not be written, so the group's "
        f"day-90 deletion was <strong>postponed</strong>. The cron retries "
        f"daily.</p><p>Error: <code>{html.escape(error[:500])}</code></p>",
        "payroll_archive_write_failed",
    )


def send_payroll_archive_refund_alert(
    og_id: str,
    checkout_session_id: str,
    payment_intent_id: str | None,
    reason: str,
    refunded: bool,
) -> bool:
    """A payroll-archive payment (#218) that cannot be honored was refunded
    (or the refund failed and needs doing by hand). `reason` is
    "duplicate" (the group already has an archive) or "group_deleted" (the
    payment landed after the day-90 deletion). Never raises.
    """
    logger.warning(
        "[OPERATOR] payroll_archive_refund og=%s session=%s pi=%s reason=%s refunded=%s",
        og_id, checkout_session_id, payment_intent_id, reason, refunded,
    )
    outcome = (
        "was refunded automatically"
        if refunded
        else "<strong>could NOT be refunded automatically: refund it in Stripe</strong>"
    )
    return _send_operator_email(
        f"[WizScheduler] Payroll archive payment {reason} for og={og_id}",
        f"<p>A payroll-archive Checkout session "
        f"(<code>{html.escape(checkout_session_id)}</code>, payment "
        f"<code>{html.escape(payment_intent_id or '?')}</code>) for "
        f"og={html.escape(og_id)} could not be honored ({html.escape(reason)}) "
        f"and {outcome}.</p>",
        "payroll_archive_refund",
    )


def send_payroll_archive_review_alert(
    og_id: str, checkout_session_id: str, payment_intent_id: str | None, reason: str
) -> bool:
    """A paid payroll-archive session (#218) arrived that cannot be matched
    to its group (say a customer mismatch). It may be genuine, so nothing
    was recorded or refunded: a person should look. Never raises."""
    logger.warning(
        "[OPERATOR] payroll_archive_needs_review og=%s session=%s pi=%s reason=%s",
        og_id, checkout_session_id, payment_intent_id, reason,
    )
    return _send_operator_email(
        f"[WizScheduler] Payroll archive payment needs review for og={og_id}",
        f"<p>A paid payroll-archive Checkout session "
        f"(<code>{html.escape(checkout_session_id)}</code>, payment "
        f"<code>{html.escape(payment_intent_id or '?')}</code>) for "
        f"og={html.escape(og_id)} could not be verified ({html.escape(reason)}). "
        f"Nothing was recorded or refunded. Check it in Stripe and decide "
        f"whether to record it or refund it.</p>",
        "payroll_archive_needs_review",
    )

def send_agent_duplicate_subscription_alert(
    og_id: str,
    duplicate_subscription_id: str,
    kept_subscription_id: str | None,
    payment_intent_id: str | None,
    amount_usd: float,
    *,
    cancelled: bool,
    refund: str,
    errors: list[str],
    superseded: bool = False,
) -> bool:
    """A second live AI host subscription (#259 step 5) arrived for a group
    that already has one. States what actually happened: whether the
    duplicate was cancelled, and whether its latest invoice was refunded
    (`refund`: "refunded", "none" = nothing paid, "failed", or "unknown" =
    the invoice could not be read). `superseded`: the subscription it
    duplicated ended and this one became the account's subscription, so
    nothing more was done. Never raises."""
    logger.warning(
        "[OPERATOR] agent_duplicate_subscription og=%s duplicate=%s kept=%s pi=%s "
        "amount=%.2f cancelled=%s refund=%s errors=%s superseded=%s",
        og_id, duplicate_subscription_id, kept_subscription_id, payment_intent_id,
        amount_usd, cancelled, refund, errors, superseded,
    )
    if superseded:
        return _send_operator_email(
            f"[WizScheduler] Duplicate AI host subscription for og={og_id} is now its subscription",
            f"<p>The duplicate AI host subscription "
            f"<code>{html.escape(duplicate_subscription_id)}</code> for "
            f"og={html.escape(og_id)} became the account's subscription (the one "
            f"it duplicated ended): no action taken. It is billing normally now. "
            f"Earlier: cancelled={'yes' if cancelled else 'no'}, refund={html.escape(refund)}.</p>",
            "agent_duplicate_subscription",
        )
    if cancelled:
        cancel_text = "was cancelled"
    else:
        cancel_text = ("<strong>could NOT be cancelled: cancel FAILED, it is still "
                       "billing. Cancel it in Stripe</strong>")
    refund_text = {
        "refunded": f"its latest invoice was refunded (${amount_usd:.2f})",
        "none": "its latest invoice had nothing paid to refund",
        "failed": (f"<strong>the refund of its latest invoice (${amount_usd:.2f}) "
                   f"FAILED: refund it in Stripe</strong>"),
    }.get(refund, ("<strong>refund status unknown (its invoice could not be read): "
                   "check Stripe</strong>"))
    detail = "".join(f"<li><code>{html.escape(e[:300])}</code></li>" for e in errors)
    return _send_operator_email(
        f"[WizScheduler] Duplicate AI host subscription for og={og_id}",
        f"<p>og={html.escape(og_id)} already has the AI host subscription "
        f"<code>{html.escape(kept_subscription_id or '?')}</code>. The second "
        f"one, <code>{html.escape(duplicate_subscription_id)}</code>, "
        f"{cancel_text}, and {refund_text} (payment "
        f"<code>{html.escape(payment_intent_id or '?')}</code>).</p>"
        + (f"<ul>{detail}</ul>" if detail else "")
        + "<p>The app retries on the subscription's next Stripe event.</p>",
        "agent_duplicate_subscription",
    )


def send_agent_final_invoice_alert(
    og_id: str, subscription_id: str, period: str | None, detail: str,
    case: str = "not_billed", invoice_id: str | None = None,
    item_ids: tuple[str, ...] = (),
) -> bool:
    """The final AI host invoice for a cancelled agent subscription (#259
    step 5) did not complete. `case` says where it stopped, because what
    the operator should do differs, and the wrong move charges twice:
      finalized_unrecorded  the invoice is finalized in Stripe, only our
                            record is missing: do nothing;
      draft_kept            a draft holds the amounts: never invoice by hand;
      nothing_owed          nothing is left to bill, only old items could
                            not be removed: never invoice by hand;
      not_billed            nothing was moved: the pending items (item_ids)
                            are still the only bill.
    Never raises."""
    logger.warning("[OPERATOR] agent_final_invoice og=%s subscription=%s period=%s case=%s "
                   "invoice=%s items=%s detail=%s", og_id, subscription_id, period, case,
                   invoice_id, ",".join(item_ids), detail)
    inv = f"<code>{html.escape(invoice_id or '?')}</code>"
    items = ", ".join(f"<code>{html.escape(i)}</code>" for i in item_ids)
    if case == "finalized_unrecorded":
        subject = "Final AI host invoice finalized but not recorded"
        action = (f"<p><strong>Invoice {inv} is already open/paid in Stripe — do nothing; "
                  f"the next run records it.</strong> Do not invoice anything by hand.</p>")
    elif case == "draft_kept":
        subject = "Final AI host invoice left as a draft"
        action = (f"<p><strong>Draft {inv} holds the amounts — do NOT invoice by hand; the "
                  f"next run finalizes it (or finalize it in Stripe).</strong> If it turns "
                  f"out to be finalized already, the next run records it.</p>")
    elif case == "nothing_owed":
        subject = "Final AI host cleanup failed"
        action = ("<p><strong>Nothing is left to bill — do NOT invoice by hand.</strong> "
                  + (f"The old pending items {items} are already covered and could not be "
                     f"removed; " if items else "")
                  + "the next run retries (or delete those items in Stripe).</p>")
    else:
        subject = "Final AI host invoice failed"
        action = ("<p>Nothing was moved or billed. The next daily run retries. To bill by "
                  "hand, invoice the existing pending AI host usage items"
                  + (f" {items}" if items else "")
                  + " in Stripe; do not create new ones (the retry skips an item that is "
                    "already on an invoice).</p>")
    return _send_operator_email(
        f"[WizScheduler] {subject} for og={og_id}",
        f"<p>The AI host subscription <code>{html.escape(subscription_id)}</code> of "
        f"og={html.escape(og_id)} ended, and its final usage invoice (period "
        f"{html.escape(period or '?')}) <strong>did not complete</strong>: "
        f"{html.escape(detail)}.</p>{action}",
        "agent_final_invoice",
    )


def send_agent_invoice_unpaid_alert(og_id: str, invoice_id: str, outcome: str,
                                    amount_usd: float, charges: int) -> bool:
    """An AI host invoice carrying usage overage was voided or marked
    uncollectible (#259 step 5): its charges are marked failed and nothing
    is billed again automatically. Never raises."""
    logger.warning("[OPERATOR] agent_invoice_unpaid og=%s invoice=%s outcome=%s amount=%.2f charges=%d",
                   og_id, invoice_id, outcome, amount_usd, charges)
    return _send_operator_email(
        f"[WizScheduler] AI host invoice {outcome} for og={og_id}",
        f"<p>The AI host invoice <code>{html.escape(invoice_id)}</code> of "
        f"og={html.escape(og_id)} was <strong>{html.escape(outcome)}</strong>. "
        f"{charges} usage overage charge(s) totalling ${amount_usd:.2f} are marked "
        f"failed. Nothing is re-billed automatically: decide in Stripe whether to "
        f"invoice it again.</p>",
        "agent_invoice_unpaid",
    )


def send_agent_comped_stripe_alert(og_id: str, subscription_id: str, event_type: str) -> bool:
    """A comped group (#259 step 11) received an event for a Stripe AI host
    subscription that is live or paid. Comped groups are never synced from
    Stripe, so the event was ignored: the operator cancels or refunds it in
    Stripe. Never raises."""
    logger.warning("[OPERATOR] agent_comped_stripe og=%s subscription=%s event=%s",
                   og_id, subscription_id, event_type)
    return _send_operator_email(
        f"[WizScheduler] Stripe AI host subscription for comped og={og_id}",
        f"<p>og={html.escape(og_id)} has a comped AI host, but Stripe sent "
        f"<code>{html.escape(event_type)}</code> for a live or paid subscription "
        f"<code>{html.escape(subscription_id)}</code>. The event was ignored and "
        f"nothing was synced. Cancel or refund it in Stripe if it should not exist.</p>",
        "agent_comped_stripe",
    )


def send_voice_numbers_unreleased_alert(og_id: str, phone_number_ids: list[str],
                                        reason: str) -> bool:
    """A deleted group's Amazon Connect numbers could not be released (#259
    step 6): Connect failed, or provisioning is off. Their rows are deleted
    with the group, so nothing will release them: release them in the
    Connect console. Never raises."""
    logger.warning("[OPERATOR] voice_numbers_unreleased og=%s ids=%s reason=%s",
                   og_id, phone_number_ids, reason)
    items = "".join(f"<li><code>{html.escape(i)}</code></li>" for i in phone_number_ids)
    return _send_operator_email(
        f"[WizScheduler] voice numbers not released for deleted og={og_id}",
        f"<p>Group og={html.escape(og_id)} was deleted but these Amazon Connect "
        f"phone numbers were not released ({html.escape(reason)}). They are still "
        f"billed: release them in the Connect console.</p><ul>{items}</ul>",
        "voice_numbers_unreleased",
    )


def send_ftc_ingest_alert(summary: dict) -> bool:
    """The weekly FTC Do Not Call ingest (#259 step 7) needs a look: the run
    raised, a file failed or held no valid number, or nothing has been
    ingested for FTC_INGEST_STALE_DAYS (the FTC URL may have moved). The
    summary carries dates and counts only, never a number. Never raises."""
    reasons = summary.get("reasons") or []
    logger.warning("[OPERATOR] ftc_ingest reasons=%s", reasons)
    items = "".join(f"<li>{html.escape(str(r))}</li>" for r in reasons)
    return _send_operator_email(
        "[WizScheduler] FTC spam list ingest needs a look",
        f"<p>The FTC Do Not Call ingest for {html.escape(str(summary.get('today')))} "
        f"needs a look. Call screening keeps using the list it has.</p><ul>{items}</ul>"
        f"<p>Check <code>{html.escape(settings.FTC_DNC_CSV_URL)}</code> still serves "
        f"the files.</p>",
        "ftc_ingest",
    )


def send_analytics_quality_alert(day: str, failures: list[str]) -> bool:
    """The nightly AI host analytics job (#259 step 10) found data-quality
    failures for `day`. Its lake partitions were already landed; its
    agent_daily_metrics were not written. `failures` carry counts and
    conversation ids only. Always logs one [OPERATOR] line. Never raises."""
    logger.error("[OPERATOR] analytics_quality_failed day=%s failures=%d", day, len(failures))
    items = "".join(f"<li>{html.escape(f)}</li>" for f in failures)
    return _send_operator_email(
        f"[WizScheduler] AI host analytics failed quality checks for {day}",
        f"<p>The nightly analytics job landed the lake for {html.escape(day)} but "
        f"<strong>did not write its daily metrics</strong>:</p><ul>{items}</ul>"
        f"<p>Fix the cause and rerun "
        f"<code>python -m backend.scripts.run_agent_analytics --date {html.escape(day)}</code>.</p>",
        "analytics_quality_failed",
    )


def send_analytics_run_failed_alert(day: str, error_type: str) -> bool:
    """The nightly AI host analytics job (#259 step 10) raised for `day`.
    Only the exception's type is sent: a database error can quote row
    values. Always logs one [OPERATOR] line. Never raises."""
    logger.error("[OPERATOR] analytics_run_failed day=%s error=%s", day, error_type)
    return _send_operator_email(
        f"[WizScheduler] AI host analytics run failed for {day}",
        f"<p>The nightly analytics job raised <code>{html.escape(error_type)}</code> "
        f"for {html.escape(day)}; see the task's logs. Its metrics for that day "
        f"were not written.</p>",
        "analytics_run_failed",
    )
