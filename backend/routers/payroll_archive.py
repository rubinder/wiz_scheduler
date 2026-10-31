"""Public, unauthenticated retrieval of a paid payroll archive (#218).

By the time anyone uses these routes the account is gone: there is no user
to log in as, no tenant context, and the request runs as the table owner
(the RLS policies in 0056 only bind app_role). Possession of a recipient
address is the only credential, so:

- POST /payroll-archive/retrieve answers the same 202 body whether or not the
  address matched anything, and mails the single-use link only to that
  address. Per source IP it 429s (in-process limiter, like the other public
  routes); per email it silently mints nothing past an hourly cap.
- GET /payroll-archive/download redeems the token once and redirects to a
  five-minute presigned URL. Every failure is the same plain 410.

Neither route looks at PAYROLL_ARCHIVE_ENABLED: an archive that was paid for
and written stays retrievable.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, status
from fastapi.responses import PlainTextResponse, RedirectResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.dependencies import get_db
from backend.services.payroll_archive import (
    mint_retrieval_tokens,
    redeem_token,
    send_retrieval_email,
)
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
    db: AsyncSession = Depends(get_db),
) -> RetrieveResponse:
    source_ip = source_ip_from_request(request)
    if not payroll_archive_retrieve_limiter.check_and_record(source_ip):
        logger.info("payroll_archive.retrieve_rate_limited ip=%s", source_ip)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Try again later.",
        )

    minted = await mint_retrieval_tokens(db, body.email)
    if minted:
        base = trusted_base_url(request)
        links = [
            (list(archive.company_names or []),
             f"{base}/api/v1/payroll-archive/download?token={raw}")
            for archive, raw in minted
        ]
        # After the response, so a match is not slower to answer than a miss.
        background.add_task(send_retrieval_email, body.email.strip().lower(), links)
    return RetrieveResponse(message=RETRIEVE_MESSAGE)


_GONE = "This download link is invalid or has expired."


@router.get("/download")
async def download_payroll_archive(
    token: str = Query(default=""),
    db: AsyncSession = Depends(get_db),
) -> Response:
    from backend.services.archive_storage import get_archive_storage

    archive = await redeem_token(db, token)
    if archive is None:
        return PlainTextResponse(_GONE, status_code=status.HTTP_410_GONE)
    try:
        written = archive.written_at
        filename = f"payroll-archive-{written.date().isoformat() if written else archive.id}.zip"
        url = get_archive_storage().presign_get(archive.s3_key, PRESIGNED_URL_SECONDS, filename)
    except Exception:
        # Leave the token unused: the failure is ours, not the link's.
        await db.rollback()
        logger.exception("payroll_archive.presign_failed archive=%s", archive.id)
        return PlainTextResponse(
            "The archive is temporarily unavailable. Try the link again later.",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    await db.commit()
    logger.info("payroll_archive.download archive=%s", archive.id)
    return RedirectResponse(url, status_code=status.HTTP_302_FOUND)
