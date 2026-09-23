"""Public, unauthenticated endpoints.

`POST /public/compliance-check` scores an uploaded schedule against the
Fair Workweek clopening (minimum rest) and advance-notice rules for the
marketing site's free "check your schedule" lead magnet (issue #116). It
takes no auth dependency, is rate-limited per source IP, and persists
nothing — not a row, not the payload in a log line.

CORS for the marketing origin is handled by the app's existing global
CORSMiddleware (backend/main.py), configured via CORS_ORIGINS
(backend/config.py) — nothing route-specific is added here.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from backend.config import settings
from backend.scheduling.rest_rules import _min_rest_violation, _rest_gap_hours
from backend.services.rate_limit import compliance_check_limiter, source_ip_from_request

logger = logging.getLogger("wizscheduler.public_compliance_check")


async def _consume_compliance_check_rate_limit(request: Request) -> None:
    """Router-level dependency, resolved before the request body is parsed
    and validated — so a malformed body still consumes a rate-limit token
    instead of getting a free 422 retry loop."""
    source_ip = source_ip_from_request(request)
    if not compliance_check_limiter.check_and_record(source_ip):
        logger.info("compliance_check.rate_limited ip=%s", source_ip)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Try again later.",
        )


router = APIRouter(
    prefix="/public",
    tags=["public"],
    dependencies=[Depends(_consume_compliance_check_rate_limit)],
)


class ComplianceShiftIn(BaseModel):
    employee: str
    start: str
    end: str


class ComplianceCheckRequest(BaseModel):
    timezone: str
    min_rest_hours: float = Field(11, gt=0)
    notice_days: float = Field(14, ge=0)
    published_at: Optional[str] = None
    shifts: List[ComplianceShiftIn] = Field(default_factory=list)


def _parse_shift_dt(raw: str, tz: ZoneInfo, index: int, field: str) -> datetime:
    """Parse one shift's start/end string, interpreting a naive value in
    *tz* and normalising an aware value into *tz* too, so every downstream
    comparison (same-day exemption included) works in the request's local
    calendar rather than whatever offset the caller happened to send.
    Raises HTTP 422 naming the offending shift index — never a raw
    exception — on anything unparseable."""
    text = (raw or "").strip()
    try:
        dt = datetime.fromisoformat(text)
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Shift {index}: could not parse {field} datetime {raw!r}.",
        )
    return dt.replace(tzinfo=tz) if dt.tzinfo is None else dt.astimezone(tz)


def _parse_published_at(raw: str, tz: ZoneInfo) -> datetime:
    text = (raw or "").strip()
    try:
        dt = datetime.fromisoformat(text)
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Could not parse published_at datetime {raw!r}.",
        )
    return dt.replace(tzinfo=tz) if dt.tzinfo is None else dt.astimezone(tz)


def _window(shift: Dict[str, datetime], tz: ZoneInfo) -> Dict[str, str]:
    """Aware ISO-8601 start/end strings, rendered in the request timezone."""
    return {
        "start": shift["start"].astimezone(tz).isoformat(),
        "end": shift["end"].astimezone(tz).isoformat(),
    }


@router.post("/compliance-check")
async def compliance_check(body: ComplianceCheckRequest, request: Request) -> dict:
    if len(body.shifts) > settings.PUBLIC_COMPLIANCE_CHECK_MAX_SHIFTS:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=(
                f"Too many shifts: {len(body.shifts)} exceeds the "
                f"{settings.PUBLIC_COMPLIANCE_CHECK_MAX_SHIFTS} limit."
            ),
        )

    try:
        tz = ZoneInfo(body.timezone)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown timezone: {body.timezone!r}.",
        )

    published_at_dt: Optional[datetime] = None
    if body.published_at:
        published_at_dt = _parse_published_at(body.published_at, tz)

    # Never log the payload itself — only a count, per issue #116.
    logger.info(
        "compliance_check.request ip=%s shift_count=%d",
        source_ip_from_request(request), len(body.shifts),
    )

    # Parse every shift up front (in input order), grouping by employee
    # while preserving each employee's first-appearance order so every
    # employee in the input ends up in the response, findings or not.
    employees_order: List[str] = []
    employees_shifts: Dict[str, List[Dict[str, datetime]]] = {}
    for idx, s in enumerate(body.shifts):
        start_dt = _parse_shift_dt(s.start, tz, idx, "start")
        end_dt = _parse_shift_dt(s.end, tz, idx, "end")
        if s.employee not in employees_shifts:
            employees_shifts[s.employee] = []
            employees_order.append(s.employee)
        employees_shifts[s.employee].append({"start": start_dt, "end": end_dt})

    total_clopenings = 0
    total_short_notice = 0
    employee_results = []

    for name in employees_order:
        shifts_sorted = sorted(employees_shifts[name], key=lambda w: w["start"])
        findings = []

        # Clopening: every pair (not just adjacent shifts) reuses the
        # shared _min_rest_violation / _rest_gap_hours helpers rather than
        # reimplementing the rest-gap + same-day-exemption logic. Adjacent
        # pairs alone miss a violation like A 16:00->next-day 02:00,
        # B same-day 17:00-18:00 (exempt), C next-day 08:00-16:00: (A,B)
        # is exempt and (B,C) clears the bar, but (A,C) is a real
        # cross-day clopening that only an every-pair scan catches. The
        # 2,000-shift request cap bounds the O(n^2)-per-employee cost.
        for i in range(len(shifts_sorted)):
            for j in range(i + 1, len(shifts_sorted)):
                earlier = shifts_sorted[i]
                later = shifts_sorted[j]
                earlier_start_iso = earlier["start"].isoformat()
                earlier_end_iso = earlier["end"].isoformat()
                later_start_iso = later["start"].isoformat()
                later_end_iso = later["end"].isoformat()
                violated = _min_rest_violation(
                    later_start_iso,
                    later_end_iso,
                    [{"start": earlier_start_iso, "end": earlier_end_iso}],
                    body.min_rest_hours,
                )
                if violated:
                    gap = _rest_gap_hours(
                        earlier_start_iso, earlier_end_iso, later_start_iso, later_end_iso
                    )
                    findings.append(
                        {
                            "kind": "clopening",
                            "first": _window(earlier, tz),
                            "second": _window(later, tz),
                            "rest_hours": round(gap, 2),
                        }
                    )
                    total_clopenings += 1

        # Short notice: every shift, only when published_at was given.
        # Uses .timestamp() (an absolute instant) rather than subtracting
        # aware datetimes directly — two datetimes sharing the same
        # ZoneInfo instance subtract as naive wall-clock time when their
        # tzinfo attributes are identical, which is wrong across a DST
        # transition between publish and the shift.
        if published_at_dt is not None:
            published_at_ts = published_at_dt.timestamp()
            for shift in shifts_sorted:
                notice_days = (shift["start"].timestamp() - published_at_ts) / 86400.0
                if notice_days < body.notice_days:
                    findings.append(
                        {
                            "kind": "short_notice",
                            "shift": _window(shift, tz),
                            "notice_days": round(notice_days, 2),
                        }
                    )
                    total_short_notice += 1

        employee_results.append({"employee": name, "findings": findings})

    return {
        "totals": {
            "employees": len(employees_order),
            "clopenings": total_clopenings,
            "short_notice": total_short_notice,
        },
        "employees": employee_results,
    }
