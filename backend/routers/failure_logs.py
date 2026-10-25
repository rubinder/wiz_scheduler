from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.dependencies import get_db, require_manager
from backend.models import FailureLog, User
from backend.schemas.failure_log import FailureLogResponse

router = APIRouter(prefix="/failure-logs", tags=["failure-logs"])


@router.get("", response_model=list[FailureLogResponse])
async def list_failure_logs(
    category: str | None = Query(None),
    severity: str | None = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> list[FailureLogResponse]:
    # Only the caller's own company. Rows with no company come from requests
    # that carried no usable token -- any tenant's, or nobody's -- so they are
    # for operators, never for a tenant's managers.
    query = select(FailureLog).where(FailureLog.company_id == current_user.company_id)

    if category:
        query = query.where(FailureLog.category == category)
    if severity:
        query = query.where(FailureLog.severity == severity)

    query = query.order_by(FailureLog.created_at.desc()).offset(offset).limit(limit)

    result = await db.execute(query)
    logs = result.scalars().all()
    return [FailureLogResponse.model_validate(log) for log in logs]
