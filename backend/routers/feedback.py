"""Manager feedback and feature request submission (rate-limited on free plan)."""
import html
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.dependencies import get_db, require_manager
from backend.models import User
from backend.services.feedback_quota import check_feedback_quota, record_feedback_submission
from backend.services.rate_limit import SlidingWindowLimiter, source_ip_from_request

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/feedback", tags=["feedback"])

feedback_limiter = SlidingWindowLimiter(max_requests=10, window_seconds=3600)


class FeedbackRequest(BaseModel):
    subject: str = Field(..., min_length=5, max_length=200)
    message: str = Field(..., min_length=10, max_length=5000)
    type: str = Field(default="general", pattern="^(bug|feature|feedback|other)$")


class FeedbackResponse(BaseModel):
    success: bool
    message: str


async def _check_feedback_rate_limit(request: Request) -> None:
    """Rate limit: 10 submissions per hour per IP."""
    source_ip = source_ip_from_request(request)
    if not feedback_limiter.check_and_record(source_ip):
        logger.warning("feedback.rate_limited ip=%s", source_ip)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many feedback submissions. Try again in a few minutes.",
        )


@router.post("/submit", response_model=FeedbackResponse)
async def submit_feedback(
    body: FeedbackRequest,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(_check_feedback_rate_limit),
) -> FeedbackResponse:
    """Submit feedback or feature request.

    Free-plan managers: 5 submissions per month.
    Paid-plan managers: unlimited submissions.
    Sends email to admin@suggestival.com with the feedback and user details.
    """
    await check_feedback_quota(db, current_user)

    if not settings.RESEND_API_KEY:
        logger.warning("RESEND_API_KEY not set — feedback submission skipped")
        return FeedbackResponse(success=False, message="Email service not configured")

    try:
        import resend

        resend.api_key = settings.RESEND_API_KEY

        email_html = f"""
        <div style="font-family: sans-serif; max-width: 600px; margin: 0 auto;">
          <h2>New Feedback Submission</h2>
          <p><strong>From:</strong> {html.escape(current_user.email)}</p>
          <p><strong>Company:</strong> {html.escape(current_user.company_id)}</p>
          <p><strong>Type:</strong> {html.escape(body.type.upper())}</p>
          <hr style="border: none; border-top: 1px solid #ddd; margin: 20px 0;">
          <h3>{html.escape(body.subject)}</h3>
          <div style="white-space: pre-wrap; color: #333;">
{html.escape(body.message)}
          </div>
          <hr style="border: none; border-top: 1px solid #ddd; margin: 20px 0;">
          <p style="color: #999; font-size: 12px;">
            This feedback was submitted via the WizScheduler app feedback form.
          </p>
        </div>
        """

        resend.Emails.send(
            {
                "from": settings.FROM_EMAIL,
                "to": ["admin@suggestival.com"],
                "reply_to": [current_user.email],
                "subject": f"[{body.type.upper()}] {body.subject}",
                "html": email_html,
            }
        )

        await record_feedback_submission(
            db,
            user_id=current_user.id,
            company_id=current_user.company_id,
            subject=body.subject,
            message=body.message,
            feedback_type=body.type,
        )
        await db.commit()

        logger.info(
            f"Feedback submitted by {current_user.email} ({current_user.company_id}): {body.type}"
        )
        return FeedbackResponse(
            success=True, message="Thank you for your feedback! We'll review it shortly."
        )

    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        logger.error(f"Failed to send feedback email: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to submit feedback. Please try again later.",
        )
