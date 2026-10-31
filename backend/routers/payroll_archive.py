"""Public, unauthenticated retrieval of a paid payroll archive (#218).

By the time anyone uses these routes the account is gone: there is no user
to log in as, no tenant context, and the request runs as the table owner
(the RLS policies in 0056 only bind app_role). Possession of a recipient
address is the only credential, so:

- POST /payroll-archive/retrieve answers the same 202 body whether or not the
  address matched anything, after only the rate-limit check: the lookup,
  token mint and email run in a background task on their own session, so
  neither the body nor the timing reveals a match. The single-use link goes
  only to that address. Per source IP it 429s (in-process limiter, like the other public
  routes); per email it silently mints nothing past an hourly cap.
- The mailed link opens the SPA page /payroll-archive/download?token=...,
  which shows a button; only POST /payroll-archive/download {token}
  redeems the token (once) and returns a five-minute presigned URL as JSON.
  A GET never redeems, so a mail scanner opening the link uses nothing up.
  Every failure is the same 410.

Neither route looks at PAYROLL_ARCHIVE_ENABLED: an archive that was paid for
and written stays retrievable.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.dependencies import get_db
from backend.services.payroll_archive import process_retrieval_request, redeem_token
from backend.services.rate_limit import payroll_archive_retrieve_limiter, source_ip_from_request
from backend.utils.base_url import trusted_base_url

router = APIRouter(prefix="/payroll-archive", tags=["payroll-archive"])
logger = logging.getLogger(__name__)

RETRIEVE_MESSAGE = (
    "If that address can retrieve a payroll archive, a download link is on "
    "its way to it."
)
PRESIGNED_URL_SECONDS = 300


class RetrieveRequest(BaseModel):
    # Not EmailStr: a malformed address must get the same answer as an
    # unknown one, and email-validator is not a dependency.
    email: str = Field(..., max_length=320)


class RetrieveResponse(BaseModel):
    message: str


@router.post("/retrieve", status_code=status.HTTP_202_ACCEPTED, response_model=RetrieveResponse)
async def retrieve_payroll_archive(
    body: RetrieveRequest,
    request: Request,
    background: BackgroundTasks,
) -> RetrieveResponse:
    source_ip = source_ip_from_request(request)
    if not payroll_archive_retrieve_limiter.check_and_record(source_ip):
        logger.info("payroll_archive.retrieve_rate_limited ip=%s", source_ip)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Try again later.",
        )

    # Everything that depends on a match (lookup, token, email) runs after
    # the response on its own session, so a hit is neither slower nor
    # different from a miss.
    background.add_task(process_retrieval_request, body.email, trusted_base_url(request))
    return RetrieveResponse(message=RETRIEVE_MESSAGE)


_GONE = "This download link is invalid or has expired."


class DownloadRequest(BaseModel):
    token: str = Field(default="", max_length=200)


class DownloadResponse(BaseModel):
    url: str


@router.post("/download", response_model=DownloadResponse)
async def download_payroll_archive(
    body: DownloadRequest,
    db: AsyncSession = Depends(get_db),
) -> DownloadResponse | JSONResponse:
    """Redeem a mailed token for a five-minute presigned URL.

    POST, never GET: corporate mail scanners (Safe Links and the like) open
    every link in an email with a GET, which would use up a single-use token
    before its recipient clicks. The mailed link opens a frontend page whose
    button makes this call. Every failure is the same 410.
    """
    from backend.services.archive_storage import get_archive_storage

    archive = await redeem_token(db, body.token)
    if archive is None:
        return JSONResponse({"detail": _GONE}, status_code=status.HTTP_410_GONE)
    try:
        written = archive.written_at
        filename = f"payroll-archive-{written.date().isoformat() if written else archive.id}.zip"
        url = get_archive_storage().presign_get(archive.s3_key, PRESIGNED_URL_SECONDS, filename)
    except Exception:
        # Leave the token unused: the failure is ours, not the link's.
        await db.rollback()
        logger.exception("payroll_archive.presign_failed archive=%s", archive.id)
        return JSONResponse(
            {"detail": "The archive is temporarily unavailable. Try again later."},
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    await db.commit()
    logger.info("payroll_archive.download archive=%s", archive.id)
    return DownloadResponse(url=url)
