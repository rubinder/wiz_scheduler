"""Feedback submission quota tracking for free-plan rate limiting."""
from datetime import datetime, timezone, timedelta

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.feedback_submission import FeedbackSubmission
from backend.models.user import User
from backend.services.plan import get_plan_state


FREE_PLAN_MONTHLY_LIMIT = 5


async def check_feedback_quota(db: AsyncSession, user: User) -> None:
    """Raise 429 if free-plan user exceeds 5 submissions/month.

    Paid-plan users have unlimited submissions.
    """
    plan_state = await get_plan_state(db, user.company_id)

    if plan_state["plan"] == "paid":
        return

    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    stmt = select(func.count(FeedbackSubmission.id)).where(
        FeedbackSubmission.user_id == user.id,
        FeedbackSubmission.created_at >= month_start,
    )

    result = await db.execute(stmt)
    count = result.scalar() or 0

    if count >= FREE_PLAN_MONTHLY_LIMIT:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "code": "feedback_quota_exceeded",
                "message": f"Free plan limited to {FREE_PLAN_MONTHLY_LIMIT} feedback submissions per month. Upgrade to Pro for unlimited submissions.",
                "limit": FREE_PLAN_MONTHLY_LIMIT,
                "current_month_count": count,
                "resets_at": (month_start + timedelta(days=32)).replace(day=1).isoformat(),
            },
        )


async def record_feedback_submission(
    db: AsyncSession,
    user_id: str,
    company_id: str,
    subject: str,
    message: str,
    feedback_type: str,
) -> FeedbackSubmission:
    """Record a feedback submission for quota tracking."""
    submission = FeedbackSubmission(
        user_id=user_id,
        company_id=company_id,
        subject=subject,
        message=message,
        feedback_type=feedback_type,
    )
    db.add(submission)
    await db.flush()
    return submission
