import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models import (
    EmployeeAvailability,
    EmployeeCheckIn,
    EmployeeInvite,
    Shift,
    ShiftSchedule,
)
from backend.models.consent import UserConsent
from backend.models.ownership_group import OwnershipGroup
from backend.models.failure_log import FailureLog

logger = logging.getLogger(__name__)


async def run_data_retention(db: AsyncSession) -> dict:
    """Execute data retention policies. Returns a summary of deleted records."""
    now = datetime.now(timezone.utc)
    summary: dict[str, int] = {}

    # 1. Rejected schedules older than configured retention period
    cutoff_rejected = now - timedelta(days=settings.RETENTION_REJECTED_SCHEDULES_DAYS)

    old_rejected = await db.execute(
        select(ShiftSchedule.id).where(
            ShiftSchedule.status == "rejected",
            ShiftSchedule.created_at < cutoff_rejected,
        )
    )
    rejected_ids = list(old_rejected.scalars().all())

    if rejected_ids:
        result = await db.execute(
            delete(Shift).where(Shift.shift_schedule_id.in_(rejected_ids))
        )
        summary["rejected_shifts_deleted"] = result.rowcount
        result = await db.execute(
            delete(ShiftSchedule).where(ShiftSchedule.id.in_(rejected_ids))
        )
        summary["rejected_schedules_deleted"] = result.rowcount
    else:
        summary["rejected_shifts_deleted"] = 0
        summary["rejected_schedules_deleted"] = 0

    # 2. Draft schedules older than configured retention period
    cutoff_drafts = now - timedelta(days=settings.RETENTION_STALE_DRAFTS_DAYS)

    old_drafts = await db.execute(
        select(ShiftSchedule.id).where(
            ShiftSchedule.status == "draft",
            ShiftSchedule.created_at < cutoff_drafts,
        )
    )
    draft_ids = list(old_drafts.scalars().all())

    if draft_ids:
        result = await db.execute(
            delete(Shift).where(Shift.shift_schedule_id.in_(draft_ids))
        )
        summary["stale_draft_shifts_deleted"] = result.rowcount
        result = await db.execute(
            delete(ShiftSchedule).where(ShiftSchedule.id.in_(draft_ids))
        )
        summary["stale_drafts_deleted"] = result.rowcount
    else:
        summary["stale_draft_shifts_deleted"] = 0
        summary["stale_drafts_deleted"] = 0

    # 3. Old availability (past configured retention period)
    cutoff_availability = now - timedelta(days=settings.RETENTION_OLD_AVAILABILITY_DAYS)
    result = await db.execute(
        delete(EmployeeAvailability).where(
            EmployeeAvailability.start_time < cutoff_availability,
        )
    )
    summary["old_availability_deleted"] = result.rowcount

    # 4. Failure logs older than configured retention period
    cutoff_failure_logs = now - timedelta(days=settings.RETENTION_FAILURE_LOGS_DAYS)
    result = await db.execute(
        delete(FailureLog).where(FailureLog.created_at < cutoff_failure_logs)
    )
    summary["old_failure_logs_deleted"] = result.rowcount

    # 5. Expired invites older than configured retention period past expiration
    cutoff_invites = now - timedelta(days=settings.RETENTION_EXPIRED_INVITES_DAYS)
    result = await db.execute(
        delete(EmployeeInvite).where(EmployeeInvite.expires_at < cutoff_invites)
    )
    summary["expired_invites_deleted"] = result.rowcount

    # 6. Revoked consents older than configured retention period
    cutoff_consents = now - timedelta(days=settings.RETENTION_REVOKED_CONSENTS_DAYS)
    result = await db.execute(
        delete(UserConsent).where(
            UserConsent.revoked_at.isnot(None),
            UserConsent.revoked_at < cutoff_consents,
        )
    )
    summary["old_revoked_consents_deleted"] = result.rowcount

    # 7. Check-ins older than the retained window. Issue #63 specifies six
    #    months of history; without this the table grows without bound and
    #    the figure is decoration.
    cutoff_check_ins = now - timedelta(days=settings.RETENTION_CHECKINS_DAYS)
    result = await db.execute(
        delete(EmployeeCheckIn).where(
            EmployeeCheckIn.checked_in_at < cutoff_check_ins
        )
    )
    summary["old_check_ins_deleted"] = result.rowcount

    # 8. Signup signals past their window. These are observe-only
    #    anti-abuse breadcrumbs (services/signup_signals.py), not account
    #    data — the ownership group stays, the columns are nulled.
    cutoff_signals = now - timedelta(days=settings.RETENTION_SIGNUP_SIGNALS_DAYS)
    result = await db.execute(
        update(OwnershipGroup)
        .where(
            OwnershipGroup.created_at < cutoff_signals,
            OwnershipGroup.signup_email_normalized.isnot(None),
        )
        .values(
            signup_ip_masked=None,
            signup_email_normalized=None,
            signup_device_id=None,
            signup_user_agent_hash=None,
        )
    )
    summary["signup_signals_cleared"] = result.rowcount

    await db.commit()

    logger.info("[DATA-RETENTION] Purge complete: %s", summary)
    return summary
