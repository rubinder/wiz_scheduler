"""Paid payroll archive (#218): offer, purchase, write, retrieve.

A cancelled ownership group inside its grace period may pay
PAYROLL_ARCHIVE_PRICE_CENTS once (Stripe Checkout, mode=payment, on its
existing customer) to have every company's full pay history written to S3 at
the day-90 deletion and kept PAYROLL_ARCHIVE_RETENTION_DAYS. The free history
export stays available and stays the default.

- Purchase is NON-REFUNDABLE. A group that reactivates keeps its purchase,
  and it is honored at a later deletion without a second charge. The one
  exception is a genuine duplicate (a second distinct paid session for a
  group that already has an archive, or a payment that lands after the group
  was deleted): that payment is refunded and the operator alerted.
- This is not an AI credit charge and never goes through
  billing.charge_saved_card (which credits the AI balance and fires the
  Anthropic top-up alert).
- PAYROLL_ARCHIVE_ENABLED gates offering and buying. Writing an archive that
  is already paid for does not look at it: a paid group is never deleted
  without its archive.
"""
from __future__ import annotations

import asyncio
import hashlib
import html
import logging
import re
import secrets
import tempfile
import zipfile
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models import Company, User
from backend.models.ownership_group import OwnershipGroup
from backend.models.payroll_archive import PayrollArchive, PayrollArchiveToken

logger = logging.getLogger(__name__)

CHECKOUT_KIND = "payroll_archive"
PRODUCT_NAME = "Payroll archive (7 years)"
# The ZIP is spooled in memory up to this size, then to a temp file.
_SPOOL_MAX_BYTES = 32 * 1024 * 1024


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def stripe_field(obj: Any, name: str) -> Any:
    """A Stripe object's field, whether it arrived as a dict (webhook
    payload) or a StripeObject / test double (API response)."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _as_id(value: Any) -> str | None:
    """`payment_intent` / `customer` may be an id or an expanded object."""
    if value is None or isinstance(value, str):
        return value
    return stripe_field(value, "id")


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def scheduled_deletion_at(og: OwnershipGroup) -> datetime | None:
    if og.canceled_at is None:
        return None
    return _aware(og.canceled_at) + timedelta(days=settings.SUBSCRIPTION_GRACE_DAYS)


def in_grace_period(og: OwnershipGroup, now: datetime | None = None) -> bool:
    """Cancelled and the deletion has not come due yet."""
    deletion = scheduled_deletion_at(og)
    if deletion is None:
        return False
    return (now or datetime.now(timezone.utc)) < deletion


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def get_archive_for_group(db: AsyncSession, og_id: str) -> PayrollArchive | None:
    return (await db.execute(
        select(PayrollArchive).where(PayrollArchive.ownership_group_id == og_id)
    )).scalar_one_or_none()


async def offer_status(db: AsyncSession, og: OwnershipGroup) -> dict:
    """What the billing UI shows. `available` only while the flag is on, the
    group is cancelled and inside its grace period, and nothing is bought."""
    archive = await get_archive_for_group(db, og.id)
    deletion = scheduled_deletion_at(og)
    return {
        "available": bool(
            settings.PAYROLL_ARCHIVE_ENABLED and archive is None and in_grace_period(og)
        ),
        "purchased": archive is not None,
        "price_usd": settings.PAYROLL_ARCHIVE_PRICE_CENTS / 100,
        "scheduled_deletion_at": deletion.isoformat() if deletion else None,
    }


async def admin_recipient_emails(db: AsyncSession, og_id: str) -> list[str]:
    """The group's admin managers, lowercase. Verified addresses when any
    admin is verified; every admin otherwise."""
    company_ids = select(Company.id).where(Company.ownership_group_id == og_id)
    admins = (await db.execute(
        select(User.email, User.email_verified_at).where(
            User.company_id.in_(company_ids),
            User.user_role == "manager",
            # NULL predates 0049 and means admin (location_scope.is_restricted_manager).
            or_(User.manager_type.is_(None), User.manager_type != "regular"),
        )
    )).all()
    verified = sorted({e.strip().lower() for e, v in admins if e and v is not None})
    if verified:
        return verified
    return sorted({e.strip().lower() for e, _ in admins if e})


async def _company_names(db: AsyncSession, og_id: str) -> list[str]:
    return list((await db.execute(
        select(Company.name).where(Company.ownership_group_id == og_id).order_by(Company.name)
    )).scalars().all())


# ---------------------------------------------------------------------------
# Purchase
# ---------------------------------------------------------------------------


class ArchiveSessionInvalid(ValueError):
    """A Checkout session that is not a paid payroll-archive session for
    this group."""


def verify_archive_session(session: Any, og: OwnershipGroup) -> None:
    """Raise ArchiveSessionInvalid unless `session` is this group's paid
    payroll-archive Checkout session."""
    metadata = stripe_field(session, "metadata") or {}
    if stripe_field(metadata, "kind") != CHECKOUT_KIND:
        raise ArchiveSessionInvalid("not a payroll archive session")
    if stripe_field(session, "client_reference_id") != og.id:
        raise ArchiveSessionInvalid("session does not belong to this account")
    if stripe_field(metadata, "ownership_group_id") not in (None, og.id):
        raise ArchiveSessionInvalid("session does not belong to this account")
    if not og.stripe_customer_id or _as_id(stripe_field(session, "customer")) != og.stripe_customer_id:
        raise ArchiveSessionInvalid("session does not belong to this account")
    if stripe_field(session, "payment_status") != "paid":
        raise ArchiveSessionInvalid("payment not completed")


def _refund(payment_intent_id: str | None, session_id: str) -> bool:
    """Refund a payroll-archive payment we cannot honor. Idempotent per
    session (Stripe idempotency key), never raises."""
    if not payment_intent_id:
        return False
    try:
        import stripe

        stripe.api_key = settings.STRIPE_SECRET_KEY
        stripe.Refund.create(
            payment_intent=payment_intent_id,
            idempotency_key=f"payroll-archive-refund-{session_id}",
        )
        return True
    except Exception as exc:
        # A redelivered webhook after the idempotency key expired (24 h)
        # finds the payment already refunded: that is a success, not a
        # refund the operator has to do by hand.
        if getattr(exc, "code", None) == "charge_already_refunded":
            return True
        logger.exception(
            "payroll_archive.refund_failed session=%s pi=%s", session_id, payment_intent_id
        )
        return False


async def insert_purchase(db: AsyncSession, og: OwnershipGroup, session: Any) -> str | None:
    """Insert the archive row for a verified paid session (not committed).
    Returns its id, or None when this session is already stored.

    The conflict target is the session id only: a second row for the same
    group must never be swallowed here (it raises instead, so a webhook is
    retried by Stripe rather than the payment silently kept). Callers hold
    the group lock and have already checked for an existing archive."""
    amount = stripe_field(session, "amount_total")
    currency = stripe_field(session, "currency")
    result = await db.execute(
        pg_insert(PayrollArchive)
        .values(
            ownership_group_id=og.id,
            company_names=await _company_names(db, og.id),
            stripe_checkout_session_id=stripe_field(session, "id"),
            stripe_payment_intent_id=_as_id(stripe_field(session, "payment_intent")),
            amount_cents=int(amount if amount is not None else settings.PAYROLL_ARCHIVE_PRICE_CENTS),
            currency=(currency or "usd").lower(),
            paid_at=datetime.now(timezone.utc),
            recipient_emails=await admin_recipient_emails(db, og.id),
            status="paid",
        )
        .on_conflict_do_nothing(index_elements=["stripe_checkout_session_id"])
        .returning(PayrollArchive.id)
    )
    return result.scalar_one_or_none()


def is_paid_archive_session_for(session: Any, og: OwnershipGroup) -> bool:
    try:
        verify_archive_session(session, og)
    except ArchiveSessionInvalid:
        return False
    return True


def _is_refunded(session: Any) -> bool:
    """True when the session's payment was fully refunded (by hand, or as a
    duplicate). Checkout's payment_status stays "paid" after a refund, so
    the charge is what says so."""
    import stripe

    pi = stripe_field(session, "payment_intent")
    if pi is None:
        return False
    if isinstance(pi, str):
        pi = stripe.PaymentIntent.retrieve(pi, expand=["latest_charge"])
    charge = stripe_field(pi, "latest_charge")
    if charge is None:
        return False
    if isinstance(charge, str):
        charge = stripe.Charge.retrieve(charge)
    return bool(stripe_field(charge, "refunded"))


def _paid_archive_sessions_sync(og: OwnershipGroup) -> list[Any]:
    if not og.stripe_customer_id or not settings.STRIPE_SECRET_KEY:
        return []
    import stripe

    stripe.api_key = settings.STRIPE_SECRET_KEY
    listing = stripe.checkout.Session.list(
        customer=og.stripe_customer_id, limit=100,
        expand=["data.payment_intent.latest_charge"],
    )
    paid = [
        s for s in listing.auto_paging_iter()
        if is_paid_archive_session_for(s, og) and not _is_refunded(s)
    ]
    paid.sort(key=lambda s: stripe_field(s, "created") or 0)
    return paid


async def paid_archive_sessions(og: OwnershipGroup) -> list[Any]:
    """This group's paid, unrefunded payroll-archive Checkout sessions in
    Stripe, oldest first: the backstop for purchases neither the return page
    nor the webhook recorded (tab closed, expired login, webhook endpoint not
    subscribed). Blocking Stripe I/O (it pages), so run off the event loop.

    Raises on a Stripe error. The cron must then not delete the group (it
    cannot prove there is no paid archive), and checkout must not offer a
    new session."""
    return await asyncio.to_thread(_paid_archive_sessions_sync, og)


async def reconcile_sessions_locked(
    db: AsyncSession, og: OwnershipGroup, sessions: list[Any]
) -> None:
    """With the group locked: store the oldest paid session if the group has
    no archive yet, then refund every other paid session (idempotent) and
    alert the operator. Not committed; commits with the write and delete."""
    from backend.services.operator_alerts import send_payroll_archive_refund_alert

    paid = [s for s in sessions if is_paid_archive_session_for(s, og)]
    if not paid:
        return
    archive = await get_archive_for_group(db, og.id)
    if archive is None:
        archive_id = await insert_purchase(db, og, paid[0])
        logger.warning(
            "payroll_archive.reconciled og=%s session=%s archive=%s",
            og.id, stripe_field(paid[0], "id"), archive_id,
        )
        kept = stripe_field(paid[0], "id")
    else:
        kept = archive.stripe_checkout_session_id
    for extra in paid:
        sid = stripe_field(extra, "id")
        if sid == kept:
            continue
        pi = _as_id(stripe_field(extra, "payment_intent"))
        send_payroll_archive_refund_alert(og.id, sid, pi, "duplicate", _refund(pi, sid))


async def sync_unrecorded_purchase(db: AsyncSession, og: OwnershipGroup) -> bool:
    """Before offering or selling the archive: record a paid session that
    was never recorded (the first stored, any further one refunded as a
    duplicate by record_archive_purchase). True when the group has bought
    the archive. Raises on a Stripe error. Commits."""
    if await get_archive_for_group(db, og.id) is not None:
        return True
    og_id = og.id
    sessions = await paid_archive_sessions(og)
    for s in sessions:
        await record_archive_purchase(db, og_id, s)
    return bool(sessions)


async def record_archive_purchase(db: AsyncSession, og_id: str, session: Any) -> str:
    """Record a paid payroll-archive Checkout session. Idempotent.

    Shared by POST /billing/payroll-archive/confirm and the
    checkout.session.completed webhook, which can arrive in either order or
    at once. The caller has already checked the session (verify_archive_session
    for confirm; the webhook re-checks it here against the locked group).

    Returns:
      "created"   -- this call stored the purchase;
      "existing"  -- this session was already stored;
      "refunded"  -- the session cannot be honored (the group already has an
                     archive from another session, or no longer exists); its
                     payment was refunded (or the operator told to) and
                     nothing was stored.

    The group's row is locked first, the same lock the day-90 cron takes
    before it reads the archive, so a purchase and a deletion cannot
    interleave: either the purchase commits first and the cron writes it,
    or the group is gone and the payment is refunded.
    """
    from backend.services.operator_alerts import send_payroll_archive_refund_alert

    session_id = stripe_field(session, "id")
    if not session_id:
        raise ArchiveSessionInvalid("session has no id")
    payment_intent_id = _as_id(stripe_field(session, "payment_intent"))

    async def _by_session() -> PayrollArchive | None:
        return (await db.execute(
            select(PayrollArchive).where(
                PayrollArchive.stripe_checkout_session_id == session_id
            )
        )).scalar_one_or_none()

    if await _by_session() is not None:
        return "existing"

    og = (await db.execute(
        select(OwnershipGroup)
        .where(OwnershipGroup.id == og_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )).scalar_one_or_none()

    if og is None:
        await db.rollback()
        refunded = _refund(payment_intent_id, session_id)
        send_payroll_archive_refund_alert(
            og_id, session_id, payment_intent_id, "group_deleted", refunded
        )
        return "refunded"

    # Re-read under the lock: a concurrent confirm/webhook may have stored
    # this session (or another) while we waited for it.
    if await _by_session() is not None:
        await db.commit()
        return "existing"
    if await get_archive_for_group(db, og.id) is not None:
        await db.commit()
        refunded = _refund(payment_intent_id, session_id)
        send_payroll_archive_refund_alert(
            og.id, session_id, payment_intent_id, "duplicate", refunded
        )
        return "refunded"

    archive_id = await insert_purchase(db, og, session)
    await db.commit()
    if archive_id is None:
        return "existing"
    logger.info(
        "payroll_archive.purchased og=%s archive=%s session=%s", og.id, archive_id, session_id
    )
    return "created"


# ---------------------------------------------------------------------------
# Write (day-90 cron)
# ---------------------------------------------------------------------------


def _csv_name(company_id: str, company_name: str) -> str:
    """One file per company; the id keeps two same-named companies apart."""
    slug = re.sub(r"[^A-Za-z0-9]+", "-", company_name).strip("-").lower()[:60] or "company"
    return f"payroll-history-{slug}-{company_id}.csv"


async def write_archive(db: AsyncSession, og: OwnershipGroup, archive: PayrollArchive) -> None:
    """Build the ZIP for a locked, paid archive row, upload it, and mark the
    row written (flushed, not committed: the caller commits it together with
    the group's deletion).

    One history CSV per company in the group, every retained record
    including archived and GDPR-erased employees (pay rows carry their own
    names). Raises on any failure; the caller rolls back and does not delete.
    """
    from backend.services.archive_storage import archive_key, get_archive_storage
    from backend.services.payroll_export import write_history_csv

    storage = get_archive_storage()

    # Recipients are refreshed now, under the row lock: an admin removed or
    # demoted during the grace period must not keep access for seven years,
    # and one added since the purchase should get it. The purchase-time list
    # stays only if the group has no admin left, so the row is never empty.
    current = await admin_recipient_emails(db, og.id)
    if current:
        archive.recipient_emails = current
    names = await _company_names(db, og.id)
    if names:
        archive.company_names = names

    companies = (await db.execute(
        select(Company.id, Company.name)
        .where(Company.ownership_group_id == og.id)
        .order_by(Company.name, Company.id)
    )).all()

    total_rows = 0
    with tempfile.SpooledTemporaryFile(max_size=_SPOOL_MAX_BYTES) as spool:
        with zipfile.ZipFile(spool, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for company_id, company_name in companies:
                with zf.open(_csv_name(company_id, company_name), "w", force_zip64=True) as member:
                    total_rows += await write_history_csv(db, company_id, member)
        size = spool.tell()
        spool.seek(0)
        key = archive_key(archive.id)
        await storage.put(key, spool, size)

    now = datetime.now(timezone.utc)
    archive.status = "written"
    archive.s3_key = key
    archive.size_bytes = size
    archive.row_count = total_rows
    archive.written_at = now
    archive.expires_at = now + timedelta(days=settings.PAYROLL_ARCHIVE_RETENTION_DAYS)
    await db.flush()
    logger.info(
        "payroll_archive.written og=%s archive=%s companies=%d rows=%d bytes=%d",
        og.id, archive.id, len(companies), total_rows, size,
    )


def send_archive_written_emails(archive: PayrollArchive) -> None:
    """Tell each recipient the archive is stored and how to retrieve it.

    Sent after the deletion commits, so no group is left to count it
    against: like the data-deleted notice it is a one-shot mandatory notice
    and bypasses the per-group cap. Resend errors are logged and swallowed.
    """
    if not settings.RESEND_API_KEY:
        logger.info("Skipping payroll-archive-written email for archive=%s (RESEND_API_KEY unset)", archive.id)
        return
    if not archive.recipient_emails or archive.expires_at is None:
        return

    retrieve_url = f"{settings.FRONTEND_URL.rstrip('/')}/payroll-archive"
    until = _aware(archive.expires_at).date().isoformat()
    names = ", ".join(archive.company_names or []) or "your account"
    try:
        import resend

        resend.api_key = settings.RESEND_API_KEY
        for recipient in archive.recipient_emails:
            resend.Emails.send({
                "from": settings.FROM_EMAIL,
                "to": [recipient],
                "subject": "Your WizScheduler payroll archive is stored",
                "html": (
                    '<div style="font-family:sans-serif;max-width:600px;margin:0 auto;">'
                    f"<p>The pay records of {html.escape(names)} have been saved "
                    f"in the payroll archive you purchased, and are stored until "
                    f"<strong>{until}</strong>.</p>"
                    f"<p>To download them, request a link at "
                    f'<a href="{html.escape(retrieve_url)}">{html.escape(retrieve_url)}</a> '
                    f"using this email address.</p>"
                    "</div>"
                ),
            })
    except Exception:
        logger.error(
            "Failed to send payroll-archive-written email for archive=%s",
            archive.id,
            exc_info=True,
        )


# ---------------------------------------------------------------------------
# Retrieve (public)
# ---------------------------------------------------------------------------


async def mint_retrieval_tokens(
    db: AsyncSession, email: str
) -> list[tuple[PayrollArchive, str]]:
    """For an address that is a recipient of written, unexpired archives,
    mint one single-use token per archive (storing only its hash) and commit.
    Returns (archive, raw token) pairs; empty when nothing matched or the
    per-email hourly cap is reached. Callers must not let the result change
    their response."""
    normalized = email.strip().lower()
    if not normalized:
        return []
    now = datetime.now(timezone.utc)
    archives = list((await db.execute(
        select(PayrollArchive).where(
            PayrollArchive.status == "written",
            PayrollArchive.expires_at > now,
            PayrollArchive.recipient_emails.contains([normalized]),
        )
    )).scalars().all())
    if not archives:
        return []

    recent = (await db.execute(
        select(PayrollArchiveToken.id).where(
            PayrollArchiveToken.email == normalized,
            PayrollArchiveToken.created_at > now - timedelta(hours=1),
        )
    )).scalars().all()
    if len(recent) >= settings.PAYROLL_ARCHIVE_RETRIEVE_PER_EMAIL_PER_HOUR * len(archives):
        logger.info("payroll_archive.retrieve_cooldown email=%s***", normalized[:3])
        return []

    minted: list[tuple[PayrollArchive, str]] = []
    for archive in archives:
        raw = secrets.token_urlsafe(32)
        db.add(PayrollArchiveToken(
            archive_id=archive.id,
            token_hash=hash_token(raw),
            email=normalized,
            created_at=now,
            expires_at=now + timedelta(hours=settings.PAYROLL_ARCHIVE_TOKEN_TTL_HOURS),
        ))
        minted.append((archive, raw))
    await db.commit()
    return minted


async def process_retrieval_request(email: str, base_url: str) -> None:
    """The whole match-dependent part of POST /payroll-archive/retrieve:
    lookup, token mint and email. Run as a background task on its own
    session, so the response (and its timing) is the same whether or not
    the address matched. Never raises."""
    from backend import database

    try:
        async with database.async_session_factory() as db:
            minted = await mint_retrieval_tokens(db, email)
        if not minted:
            return
        links = [
            (list(archive.company_names or []),
             f"{base_url}/payroll-archive/download?token={raw}")
            for archive, raw in minted
        ]
        send_retrieval_email(email.strip().lower(), links)
    except Exception:
        logger.exception("payroll_archive.retrieve_failed")


def send_retrieval_email(email: str, links: list[tuple[list[str], str]]) -> None:
    """Mail the download links (one per archive) to the address that asked.
    No group is left to cap it against. Never raises."""
    if not settings.RESEND_API_KEY:
        logger.info("Skipping payroll-archive retrieval email (RESEND_API_KEY unset)")
        return
    items = "".join(
        f'<li>{html.escape(", ".join(names) or "Payroll archive")}: '
        f'<a href="{html.escape(url)}">download</a></li>'
        for names, url in links
    )
    try:
        import resend

        resend.api_key = settings.RESEND_API_KEY
        resend.Emails.send({
            "from": settings.FROM_EMAIL,
            "to": [email],
            "subject": "Your WizScheduler payroll archive download link",
            "html": (
                '<div style="font-family:sans-serif;max-width:600px;margin:0 auto;">'
                "<p>Someone (hopefully you) asked to download a WizScheduler "
                "payroll archive for this address.</p>"
                f"<ul>{items}</ul>"
                f"<p>Each link works once and expires in "
                f"{settings.PAYROLL_ARCHIVE_TOKEN_TTL_HOURS} hours. If you did "
                f"not ask for this, you can ignore this email.</p>"
                "</div>"
            ),
        })
    except Exception:
        logger.error("Failed to send payroll-archive retrieval email", exc_info=True)


async def redeem_token(db: AsyncSession, raw_token: str) -> PayrollArchive | None:
    """Mark a valid, unexpired, unused token used and return its written,
    unexpired archive. None (and nothing changed) otherwise. The conditional
    UPDATE makes two concurrent clicks redeem it at most once. Not committed:
    the caller commits once the redirect URL is in hand."""
    if not raw_token:
        return None
    now = datetime.now(timezone.utc)
    archive_id = (await db.execute(
        update(PayrollArchiveToken)
        .where(
            PayrollArchiveToken.token_hash == hash_token(raw_token),
            PayrollArchiveToken.used_at.is_(None),
            PayrollArchiveToken.expires_at > now,
        )
        .values(used_at=now)
        .returning(PayrollArchiveToken.archive_id)
    )).scalar_one_or_none()
    if archive_id is None:
        return None
    archive = await db.get(PayrollArchive, archive_id)
    if (
        archive is None
        or archive.status != "written"
        or not archive.s3_key
        or archive.expires_at is None
        or _aware(archive.expires_at) <= now
    ):
        await db.rollback()
        return None
    return archive
