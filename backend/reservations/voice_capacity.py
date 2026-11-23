"""Voice call capacity (decision 103): how many AI host calls can be up at
once, platform-wide and per company.

The platform limit is Amazon Connect's concurrent-calls quota. It is raised
step-wise (`ensure_quota`) toward a target: the most of
VOICE_QUOTA_BASE_CALLS + VOICE_QUOTA_CALLS_PER_COMPANY x companies with voice
on, the 30-day platform peak x VOICE_QUOTA_FORECAST_MULTIPLIER, and the sum
of the companies' allocations. A target above VOICE_QUOTA_AUTO_REQUEST_MAX is
never filed automatically: the operator is mailed a support-case text (the
AWS Support API needs a paid plan and is never called). AWS quotas only go
up; "down" applies to our own allocations alone.

Each company holds at most its allocation of simultaneous calls
(`at_allocation`, checked by the voice bridge before a new call). The
allocation changes only by an operator's approval of a capacity request
(`approve_request`, run by backend/scripts/approve_capacity_request.py) or
the nightly down-shift (`down_shift`).

The nightly step (`run_daily`, after the voice numbers step of the daily AI
host run) records yesterday's peaks, down-shifts, sweeps old peaks and
re-checks the quota. With VOICE_PROVISIONING_ENABLED off it does nothing,
and neither does `ensure_quota`. AWS is reached only through
services.voice_quotas; nothing here raises on an AWS failure (the operator
is mailed instead). Peaks hold counts only, never a diner identifier.
"""

from __future__ import annotations

import html
import logging
import math
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models import Location, User
from backend.reservations.agent.models import AgentConversation, AgentEvent
from backend.reservations.subscription import ANSWERING_STATUSES
from backend.reservations.subscription_models import COMPED, ReservationSubscription
from backend.reservations.voice_capacity_models import (
    OPEN_QUOTA_REQUEST_STATUSES, VoiceCapacityDaily, VoiceCapacityRequest,
    VoiceCompanySettings, VoicePlatformDaily, VoiceQuotaRequest,
)
from backend.reservations.voice_number_models import VoiceNumber
from backend.services import connect_numbers
from backend.services.archive import active
from backend.services.voice_quotas import QuotaClient, get_quota_client
from backend.tenant import tenant_scope_disabled

logger = logging.getLogger(__name__)

PEAK_WINDOW_DAYS = 30
DOWN_SHIFT_BELOW = 0.5  # of the allocation
DOWN_SHIFT_HEADROOM = 1.5  # x the 30-day peak
MAX_EXPECTED_PEAK = 500
# Numbers in these states mean the company has voice on.
VOICE_ON_STATUSES = ("requested", "active")


class CapacityRequestError(Exception):
    """A capacity request cannot be made or decided."""


class CapacityRequestPending(CapacityRequestError):
    """The company already has a request waiting for the operator."""


class UnknownLocation(CapacityRequestError):
    """A location that is not one of the company's active locations."""


# -- mail ---------------------------------------------------------------------

def _send(to: list[str], subject: str, body_html: str) -> None:
    """Via Resend; no-op without RESEND_API_KEY; never raises."""
    if not to or not settings.RESEND_API_KEY:
        return
    try:
        import resend

        resend.api_key = settings.RESEND_API_KEY
        resend.Emails.send({
            "from": settings.FROM_EMAIL, "to": to, "subject": subject,
            "html": ('<div style="font-family:sans-serif;max-width:600px;margin:0 auto;">'
                     f"{body_html}</div>"),
        })
    except Exception:
        logger.exception("voice capacity email failed")


def _mail_operator(subject: str, body_html: str) -> None:
    from backend.services.operator_alerts import _send_operator_email

    _send_operator_email(f"[WizScheduler] {subject}", body_html, "voice_capacity")


async def _mail_admins(db: AsyncSession, company_id: str, subject: str,
                       body_html: str) -> None:
    """The company's admins (managers who are not 'regular')."""
    with tenant_scope_disabled():
        emails = (await db.execute(
            select(User.email).where(
                User.company_id == company_id, User.user_role == "manager",
                or_(User.manager_type.is_(None), User.manager_type != "regular"))
            .order_by(User.email))).scalars().all()
    _send(list(emails), subject, body_html)


def _e(value: Any) -> str:
    return html.escape(str(value))


# -- allocation -----------------------------------------------------------------

async def company_settings(db: AsyncSession, company_id: str) -> VoiceCompanySettings | None:
    return (await db.execute(select(VoiceCompanySettings).where(
        VoiceCompanySettings.company_id == company_id))).scalar_one_or_none()


async def allocation_for(db: AsyncSession, company_id: str) -> int:
    row = await company_settings(db, company_id)
    return row.voice_call_allocation if row else settings.VOICE_DEFAULT_COMPANY_ALLOCATION


async def _set_allocation(db: AsyncSession, company_id: str, n: int, now: datetime) -> None:
    row = await company_settings(db, company_id)
    if row is None:
        db.add(VoiceCompanySettings(company_id=company_id, voice_call_allocation=n,
                                    allocation_changed_at=now))
    else:
        row.voice_call_allocation = n
        row.allocation_changed_at = now
    await db.flush()


async def default_transfer_number(db: AsyncSession, company_id: str) -> str | None:
    row = await company_settings(db, company_id)
    return row.default_staff_transfer_number if row else None


async def set_default_transfer_number(db: AsyncSession, company_id: str,
                                      number: str | None) -> None:
    """Validated by the caller (voice_transfers.number_problem). Does not
    commit."""
    row = await company_settings(db, company_id)
    if row is None:
        db.add(VoiceCompanySettings(
            company_id=company_id,
            voice_call_allocation=settings.VOICE_DEFAULT_COMPANY_ALLOCATION,
            default_staff_transfer_number=number))
    else:
        row.default_staff_transfer_number = number
    await db.flush()


async def open_calls(db: AsyncSession, company_id: str, now: datetime,
                     exclude_conversation_id: str | None = None) -> int:
    """The company's voice calls up right now: open voice conversations with
    a turn in the last VOICE_CALL_IDLE_SECONDS (a call whose caller hung up
    stays open, since Connect does not tell us)."""
    since = now - timedelta(seconds=settings.VOICE_CALL_IDLE_SECONDS)
    q = select(func.count()).select_from(AgentConversation).where(
        AgentConversation.company_id == company_id,
        AgentConversation.channel == "voice",
        AgentConversation.status == "open",
        AgentConversation.updated_at >= since)
    if exclude_conversation_id is not None:
        q = q.where(AgentConversation.id != exclude_conversation_id)
    return int((await db.execute(q)).scalar_one())


async def at_allocation(db: AsyncSession, company_id: str, now: datetime,
                        exclude_conversation_id: str | None = None) -> bool:
    """The company already holds its allocation of calls: a new one is
    refused."""
    return await open_calls(db, company_id, now, exclude_conversation_id) >= \
        await allocation_for(db, company_id)


# -- peaks ---------------------------------------------------------------------------

def day_peak(intervals: list[tuple[datetime, datetime]], start: datetime,
             end: datetime) -> int:
    """The most intervals open at once within [start, end). Touching
    intervals count as overlapping, so capacity errs high."""
    points: list[tuple[datetime, int]] = []
    for a, b in intervals:
        a, b = max(a, start), min(b, end)
        if a >= end or b < start or b < a:
            continue
        points += [(a, 0), (b, 1)]  # starts sort before ends at a tie
    peak = n = 0
    for _, kind in sorted(points):
        n += 1 if kind == 0 else -1
        peak = max(peak, n)
    return peak


async def _intervals(db: AsyncSession, start: datetime,
                     end: datetime) -> list[tuple[str, datetime, datetime]]:
    """(company, start, end) of each voice conversation touching the day; an
    open one ends at its last event (or is an instant without one)."""
    last_event = (select(func.max(AgentEvent.created_at))
                  .where(AgentEvent.conversation_id == AgentConversation.id)
                  .correlate(AgentConversation).scalar_subquery())
    ends = func.coalesce(AgentConversation.ended_at, last_event, AgentConversation.started_at)
    rows = (await db.execute(
        select(AgentConversation.company_id, AgentConversation.started_at, ends)
        .where(AgentConversation.channel == "voice",
               AgentConversation.started_at < end, ends >= start))).all()
    return [(c, s, max(s, e)) for c, s, e in rows]


async def record_peaks(db: AsyncSession, day: date, client: QuotaClient | None) -> dict:
    """Write `day`'s (UTC) peak and call count per company and for the
    platform, replacing any earlier run for that day. The platform peak is
    the overlap across all companies, not the sum of their peaks. With a
    client, CloudWatch's ConcurrentCalls maximum is stored beside it."""
    start = datetime.combine(day, time(0), tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    with tenant_scope_disabled():
        rows = await _intervals(db, start, end)
        by_company: dict[str, list[tuple[datetime, datetime]]] = {}
        calls: dict[str, int] = {}
        for company, s, e in rows:
            by_company.setdefault(company, []).append((s, e))
            if start <= s < end:
                calls[company] = calls.get(company, 0) + 1
        await db.execute(delete(VoiceCapacityDaily).where(VoiceCapacityDaily.day == day))
        await db.execute(delete(VoicePlatformDaily).where(VoicePlatformDaily.day == day))
        for company, spans in sorted(by_company.items()):
            db.add(VoiceCapacityDaily(company_id=company, day=day,
                                      peak_concurrent=day_peak(spans, start, end),
                                      calls=calls.get(company, 0)))
        cloudwatch: int | None = None
        if client is not None:
            try:
                cloudwatch = await client.peak_concurrent_calls(day)
            except Exception:  # noqa: BLE001 - our own peak still stands
                logger.exception("voice capacity: CloudWatch peak unavailable for %s", day)
        platform = day_peak([(s, e) for _, s, e in rows], start, end)
        db.add(VoicePlatformDaily(day=day, peak_concurrent=platform,
                                  cloudwatch_peak=cloudwatch, calls=sum(calls.values())))
        await db.commit()
    return {"day": day.isoformat(), "companies": len(by_company),
            "platform_peak": platform, "cloudwatch_peak": cloudwatch}


async def _company_peak(db: AsyncSession, company_id: str,
                        today: date) -> VoiceCapacityDaily | None:
    return (await db.execute(
        select(VoiceCapacityDaily)
        .where(VoiceCapacityDaily.company_id == company_id,
               VoiceCapacityDaily.day >= today - timedelta(days=PEAK_WINDOW_DAYS))
        .order_by(VoiceCapacityDaily.peak_concurrent.desc(), VoiceCapacityDaily.day.desc())
        .limit(1))).scalar_one_or_none()


# -- the quota ------------------------------------------------------------------------

async def _voice_companies(db: AsyncSession) -> list[str]:
    """Companies with voice on: a number requested or active, under an
    answering, paid AI host subscription."""
    return list((await db.execute(
        select(VoiceNumber.company_id).distinct()
        .join(ReservationSubscription, ReservationSubscription.ownership_group_id
              == VoiceNumber.ownership_group_id)
        .where(VoiceNumber.status.in_(VOICE_ON_STATUSES),
               ReservationSubscription.status.in_(ANSWERING_STATUSES),
               ReservationSubscription.status != COMPED)
        .order_by(VoiceNumber.company_id))).scalars().all())


async def quota_target(db: AsyncSession, now: datetime) -> int:
    """The concurrent-calls quota the platform should have (module notes)."""
    today = now.astimezone(timezone.utc).date()
    with tenant_scope_disabled():
        companies = await _voice_companies(db)
        peak = (await db.execute(
            select(func.max(func.greatest(
                VoicePlatformDaily.peak_concurrent,
                func.coalesce(VoicePlatformDaily.cloudwatch_peak, 0))))
            .where(VoicePlatformDaily.day >= today - timedelta(days=PEAK_WINDOW_DAYS))
        )).scalar_one_or_none() or 0
        allocations = dict((await db.execute(
            select(VoiceCompanySettings.company_id, VoiceCompanySettings.voice_call_allocation)
            .where(VoiceCompanySettings.company_id.in_(companies)))).all()) if companies else {}
    base = settings.VOICE_QUOTA_BASE_CALLS + settings.VOICE_QUOTA_CALLS_PER_COMPANY * len(companies)
    forecast = math.ceil(peak * settings.VOICE_QUOTA_FORECAST_MULTIPLIER)
    allocated = sum(allocations.get(c, settings.VOICE_DEFAULT_COMPANY_ALLOCATION)
                    for c in companies)
    return max(base, forecast, allocated)


def support_case_text(applied: int | None, desired: int) -> str:
    """What the operator pastes into an AWS support case (Service Quotas
    console, or Support Center) for a target above the auto-request max."""
    instance = settings.CONNECT_INSTANCE_ARN or "(the AI host Connect instance)"
    now_applied = "unknown" if applied is None else str(applied)
    return (f"Service: Amazon Connect. Quota: {settings.VOICE_QUOTA_NAME}. "
            f"Instance: {instance}. Current value: {now_applied}. "
            f"Requested value: {desired}. Use case: an automated restaurant "
            "reservations phone line serving several restaurants; the requested "
            "value covers our measured peak simultaneous calls with headroom and "
            "the capacity our customers have reserved.")


async def ensure_quota(db: AsyncSession, now: datetime | None = None,
                       client: QuotaClient | None = None, reason: str = "") -> dict:
    """Raise the quota toward `quota_target` if it is short and no request is
    open; poll open requests first. Never raises: a failure mails the
    operator the exact value to request. A no-op while provisioning is off."""
    if not connect_numbers.enabled():
        return {"disabled": True}
    now = now or datetime.now(timezone.utc)
    client = client or get_quota_client()
    target: int | None = None
    try:
        target = await quota_target(db, now)
        quota = await client.applied_quota()
        if quota is None:
            _mail_operator(
                "Voice call quota not found",
                f"<p>Service Quotas has no Amazon Connect quota named "
                f"<b>{_e(settings.VOICE_QUOTA_NAME)}</b>. Request "
                f"<b>{target}</b> concurrent calls by hand, and check VOICE_QUOTA_NAME.</p>")
            return {"error": "quota_not_found", "target": target}
        with tenant_scope_disabled():
            open_rows = (await db.execute(
                select(VoiceQuotaRequest)
                .where(VoiceQuotaRequest.status.in_(OPEN_QUOTA_REQUEST_STATUSES))
                .order_by(VoiceQuotaRequest.created_at))).scalars().all()
            for row in open_rows:
                if row.status == "pending" and row.aws_request_id:
                    status = await client.request_status(quota, row.aws_request_id)
                    if status != "pending":
                        row.status = status
                if row.status in OPEN_QUOTA_REQUEST_STATUSES and quota.value >= row.desired:
                    row.status = "approved"
            still_open = [r for r in open_rows if r.status in OPEN_QUOTA_REQUEST_STATUSES]
            await db.commit()
            out: dict[str, Any] = {"applied": quota.value, "target": target}
            if quota.value >= target:
                return {**out, "action": "none"}
            if still_open:
                return {**out, "action": "pending"}
            why = reason or "nightly check"
            if target > settings.VOICE_QUOTA_AUTO_REQUEST_MAX:
                db.add(VoiceQuotaRequest(desired=target, status="manual", reason=why))
                await db.commit()
                _mail_operator(
                    "Voice call quota needs a support case",
                    f"<p>The AI host needs <b>{target}</b> concurrent calls (now "
                    f"{quota.value}), above the {settings.VOICE_QUOTA_AUTO_REQUEST_MAX} "
                    "we file automatically. Open an AWS support case with this text:</p>"
                    f"<blockquote>{_e(support_case_text(quota.value, target))}</blockquote>")
                return {**out, "action": "manual"}
            request_id = await client.request_increase(quota, target)
            db.add(VoiceQuotaRequest(desired=target, status="pending",
                                     aws_request_id=request_id or None, reason=why))
            await db.commit()
        _mail_operator(
            "Voice call quota increase requested",
            f"<p>Requested Amazon Connect <b>{_e(settings.VOICE_QUOTA_NAME)}</b> "
            f"from {quota.value} to <b>{target}</b> ({_e(why)}). Request id "
            f"{_e(request_id)}. Nothing to do unless AWS asks for details.</p>")
        return {**out, "action": "requested", "request_id": request_id}
    except Exception as e:  # noqa: BLE001 - never raise; the operator is told
        logger.exception("voice capacity: quota step failed")
        try:
            await db.rollback()
        except Exception:  # noqa: BLE001
            logger.exception("voice capacity: rollback failed")
        wanted = "the target (could not compute it)" if target is None else str(target)
        _mail_operator(
            "Voice call quota step failed",
            f"<p>The voice call quota step failed ({_e(type(e).__name__)}). Request "
            f"Amazon Connect <b>{_e(settings.VOICE_QUOTA_NAME)}</b> = <b>{wanted}</b> "
            "in the Service Quotas console.</p>")
        return {"error": f"{type(e).__name__}: {str(e)[:200]}", "target": target}


# -- down-shift ---------------------------------------------------------------------------

async def down_shift(db: AsyncSession, now: datetime) -> list[dict]:
    """Lower each allocation above the default whose 30-day peak stayed below
    half of it, to max(default, ceil(peak x 1.5)), and tell the company's
    admins. An allocation changed in the last 30 days is left alone (a fresh
    approval has had no time to be used)."""
    today = now.astimezone(timezone.utc).date()
    default = settings.VOICE_DEFAULT_COMPANY_ALLOCATION
    changed: list[dict] = []
    with tenant_scope_disabled():
        rows = (await db.execute(
            select(VoiceCompanySettings)
            .where(VoiceCompanySettings.voice_call_allocation > default,
                   or_(VoiceCompanySettings.allocation_changed_at.is_(None),
                       VoiceCompanySettings.allocation_changed_at
                       < now - timedelta(days=PEAK_WINDOW_DAYS)))
            .order_by(VoiceCompanySettings.company_id))).scalars().all()
        for row in rows:
            best = await _company_peak(db, row.company_id, today)
            peak = best.peak_concurrent if best else 0
            if peak >= row.voice_call_allocation * DOWN_SHIFT_BELOW:
                continue
            new = max(default, math.ceil(peak * DOWN_SHIFT_HEADROOM))
            if new >= row.voice_call_allocation:
                continue
            changed.append({"company": row.company_id, "from": row.voice_call_allocation,
                            "to": new})
            row.voice_call_allocation = new
            row.allocation_changed_at = now
        await db.commit()
        for c in changed:
            await _mail_admins(
                db, c["company"], "Your AI host call capacity was adjusted",
                f"<p>Your AI host phone line was reserved for {c['from']} simultaneous "
                f"calls, and the busiest moment in the last {PEAK_WINDOW_DAYS} days used "
                f"fewer than half. It now holds <b>{c['to']}</b>. If you expect a busier "
                "period, request more capacity on the AI host billing page.</p>")
    return changed


# -- the daily run --------------------------------------------------------------------------

async def run_daily(db: AsyncSession, now: datetime | None = None,
                    client: QuotaClient | None = None) -> dict:
    """Yesterday's peaks, the down-shift, the retention sweep, then the quota
    check. A no-op while provisioning is off."""
    if not connect_numbers.enabled():
        return {"disabled": True}
    now = now or datetime.now(timezone.utc)
    client = client or get_quota_client()
    yesterday = now.astimezone(timezone.utc).date() - timedelta(days=1)
    out: dict[str, Any] = {"errors": []}
    try:
        out["peaks"] = await record_peaks(db, yesterday, client)
    except Exception as e:  # noqa: BLE001 - the rest still runs
        logger.exception("voice capacity: peaks not recorded")
        await db.rollback()
        out["errors"].append({"step": "peaks", "error": f"{type(e).__name__}: {str(e)[:200]}"})
    try:
        out["down_shift"] = await down_shift(db, now)
    except Exception as e:  # noqa: BLE001
        logger.exception("voice capacity: down-shift failed")
        await db.rollback()
        out["errors"].append({"step": "down_shift",
                              "error": f"{type(e).__name__}: {str(e)[:200]}"})
    cutoff = now.astimezone(timezone.utc).date() - timedelta(
        days=settings.RETENTION_VOICE_CAPACITY_DAYS)
    with tenant_scope_disabled():
        await db.execute(delete(VoiceCapacityDaily).where(VoiceCapacityDaily.day < cutoff))
        await db.execute(delete(VoicePlatformDaily).where(VoicePlatformDaily.day < cutoff))
        await db.commit()
    out["quota"] = await ensure_quota(db, now, client, "nightly check")
    return out


# -- capacity requests ----------------------------------------------------------------------

async def _pending(db: AsyncSession, company_id: str) -> VoiceCapacityRequest | None:
    return (await db.execute(
        select(VoiceCapacityRequest)
        .where(VoiceCapacityRequest.company_id == company_id,
               VoiceCapacityRequest.status == "pending")
        .order_by(VoiceCapacityRequest.created_at.desc()).limit(1))).scalar_one_or_none()


async def request_capacity(db: AsyncSession, company_id: str, user_id: str | None,
                           location_ids: list[str], expected_peak: int,
                           needed_by: date | None, note: str,
                           now: datetime) -> VoiceCapacityRequest:
    """Record an admin's request (validated by the route's schema) and mail
    the operator the company's measured peaks. One pending request per
    company. Commits."""
    if await _pending(db, company_id) is not None:
        raise CapacityRequestPending("capacity_request_pending")
    wanted = sorted(set(location_ids))
    names = dict((await db.execute(
        select(Location.id, Location.name)
        .where(Location.company_id == company_id, Location.id.in_(wanted),
               active(Location)))).all())
    if not wanted or set(names) != set(wanted):
        raise UnknownLocation("unknown_location")
    req = VoiceCapacityRequest(company_id=company_id, requested_by=user_id,
                               location_ids=wanted, expected_peak=expected_peak,
                               needed_by=needed_by, note=note)
    db.add(req)
    await db.flush()
    today = now.astimezone(timezone.utc).date()
    recent = (await db.execute(
        select(VoiceCapacityDaily.day, VoiceCapacityDaily.peak_concurrent,
               VoiceCapacityDaily.calls)
        .where(VoiceCapacityDaily.company_id == company_id,
               VoiceCapacityDaily.day >= today - timedelta(days=PEAK_WINDOW_DAYS))
        .order_by(VoiceCapacityDaily.day.desc()))).all()
    allocation = await allocation_for(db, company_id)
    await db.commit()
    peak = max((p for _, p, _ in recent), default=0)
    days = "".join(f"<li>{d.isoformat()}: {p} at once, {c} calls</li>" for d, p, c in recent[:14])
    _mail_operator(
        "AI host capacity request",
        f"<p>Company {_e(company_id)} asks for <b>{expected_peak}</b> simultaneous calls "
        f"(allocation now {allocation}; busiest in {PEAK_WINDOW_DAYS} days: {peak}).</p>"
        f"<p>Locations: {_e(', '.join(names[i] for i in wanted))}. Needed by: "
        f"{_e(needed_by.isoformat() if needed_by else 'not given')}.</p>"
        f"<p>Note: {_e(note) or '(none)'}</p><ul>{days}</ul>"
        f"<p>Approve: <code>python -m backend.scripts.approve_capacity_request "
        f"{_e(req.id)} [--allocation N]</code>; decline with <code>--decline</code>.</p>")
    return req


async def approve_request(db: AsyncSession, request_id: str, allocation: int | None,
                          now: datetime | None = None,
                          client: QuotaClient | None = None) -> dict:
    """The operator approves: set the company's allocation (default: what it
    asked for), then bring the quota up to cover it (`ensure_quota`, which
    files or mails the support-case text), and tell the company's admins."""
    now = now or datetime.now(timezone.utc)
    with tenant_scope_disabled():
        req = await db.get(VoiceCapacityRequest, request_id)
        if req is None or req.status != "pending":
            raise CapacityRequestError(f"no pending capacity request {request_id}")
        grant = allocation or req.expected_peak
        if grant < 1:
            raise CapacityRequestError("allocation must be at least 1")
        company_id = req.company_id
        await _set_allocation(db, company_id, grant, now)
        req.status, req.granted_allocation, req.decided_at = "approved", grant, now
        await db.commit()
    quota = await ensure_quota(db, now, client, f"capacity request {request_id}")
    await _mail_admins(
        db, company_id, "Your AI host call capacity request was approved",
        f"<p>Your AI host phone line can now take <b>{grant}</b> simultaneous calls.</p>")
    return {"request": request_id, "company": company_id, "allocation": grant,
            "quota": quota}


async def decline_request(db: AsyncSession, request_id: str,
                          now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    with tenant_scope_disabled():
        req = await db.get(VoiceCapacityRequest, request_id)
        if req is None or req.status != "pending":
            raise CapacityRequestError(f"no pending capacity request {request_id}")
        req.status, req.decided_at = "declined", now
        company_id = req.company_id
        await db.commit()
    allocation = await allocation_for(db, company_id)
    await _mail_admins(
        db, company_id, "Your AI host call capacity request",
        f"<p>We could not raise your AI host phone capacity this time; it stays at "
        f"{allocation} simultaneous calls. Reply to this email to talk it through.</p>")
    return {"request": request_id, "company": company_id, "declined": True}


# -- the billing card -----------------------------------------------------------------------

async def capacity_summary(db: AsyncSession, company_id: str, now: datetime) -> dict:
    """What the AI host billing page's "Call capacity" card shows."""
    today = now.astimezone(timezone.utc).date()
    locations = (await db.execute(
        select(Location.id, Location.name)
        .join(VoiceNumber, and_(VoiceNumber.location_id == Location.id,
                                VoiceNumber.company_id == company_id))
        .where(Location.company_id == company_id, active(Location),
               VoiceNumber.status.in_(VOICE_ON_STATUSES))
        .order_by(Location.name))).all()
    best = await _company_peak(db, company_id, today)
    pending = await _pending(db, company_id)
    return {
        "voice_locations": [{"location_id": i, "name": n} for i, n in locations],
        "allocation": await allocation_for(db, company_id),
        "busiest": ({"peak_concurrent": best.peak_concurrent, "day": best.day.isoformat()}
                    if best else None),
        "pending_request": ({"expected_peak": pending.expected_peak,
                             "created_at": pending.created_at.isoformat()}
                            if pending else None),
        "default_staff_transfer_number": await default_transfer_number(db, company_id),
        "max_expected_peak": MAX_EXPECTED_PEAK,
    }
