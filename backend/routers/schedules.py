import json
import logging
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from backend.dependencies import get_db, require_manager
from backend.models import Shift, ShiftSchedule, User
from backend.models.consent import UserConsent
from backend.utils.privacy import mask_ip
from backend.models.employee import EmployeeAvailability
from backend.schemas.schedule import GenerateRequest, ShiftScheduleResponse, UpdateShiftsRequest
from backend.services.schedule_lock import LockHeld, acquire as acquire_lock, release as release_lock
from backend.utils.id_gen import generate_short_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/schedules", tags=["schedules"])


async def _subtract_availability_for_shifts(
    db: AsyncSession,
    company_id: str,
    shifts_data: list[dict],
) -> None:
    """Remove scheduled hours from employee availability windows.

    For each shift, find the availability window that covers it on the same
    day.  Delete that window and insert replacement windows for any remaining
    time before/after the shift.

    Availability is stored as local times tagged as UTC, so comparisons use
    HH:MM on matching dates (consistent with the scheduling pipeline).
    """
    for s in shifts_data:
        emp_id = s.get("employee_id", "")
        if not emp_id or emp_id == "VACANT":
            continue

        try:
            shift_start = datetime.fromisoformat(s["start_time"])
            shift_end = datetime.fromisoformat(s["end_time"])
            shift_date = date.fromisoformat(s["date"]) if isinstance(s["date"], str) else s["date"]
        except (ValueError, KeyError):
            continue

        shift_start_hm = shift_start.strftime("%H:%M")
        shift_end_hm = shift_end.strftime("%H:%M")

        # Find availability windows for this employee on the shift date
        avail_result = await db.execute(
            select(EmployeeAvailability).where(
                EmployeeAvailability.employee_id == emp_id,
                EmployeeAvailability.year == shift_date.year,
                EmployeeAvailability.month == shift_date.month,
                EmployeeAvailability.day == shift_date.day,
            )
        )
        avail_rows = avail_result.scalars().all()

        for avail in avail_rows:
            w_start_hm = avail.start_time.strftime("%H:%M")
            w_end_hm = avail.end_time.strftime("%H:%M")

            # Check if this window covers (overlaps with) the shift
            if w_start_hm >= shift_end_hm or w_end_hm <= shift_start_hm:
                continue  # no overlap

            # This window overlaps — delete it and create remnants
            await db.delete(avail)

            # Remnant before the shift: window_start .. shift_start
            if w_start_hm < shift_start_hm:
                before = EmployeeAvailability(
                    id=generate_short_id(),
                    company_id=avail.company_id,
                    employee_id=emp_id,
                    year=avail.year,
                    month=avail.month,
                    day=avail.day,
                    start_time=avail.start_time,
                    end_time=avail.start_time.replace(
                        hour=shift_start.hour, minute=shift_start.minute, second=0,
                    ),
                )
                db.add(before)

            # Remnant after the shift: shift_end .. window_end
            if w_end_hm > shift_end_hm:
                after = EmployeeAvailability(
                    id=generate_short_id(),
                    company_id=avail.company_id,
                    employee_id=emp_id,
                    year=avail.year,
                    month=avail.month,
                    day=avail.day,
                    start_time=avail.start_time.replace(
                        hour=shift_end.hour, minute=shift_end.minute, second=0,
                    ),
                    end_time=avail.end_time,
                )
                db.add(after)

    await db.flush()


@router.get("/ai-credits")
async def get_ai_credits(
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Check AI credit status for the current ownership group."""
    from backend.services.billing import check_ai_credits

    return await check_ai_credits(db, str(current_user.company_id))


@router.get("/schedule-quota")
async def get_schedule_quota(
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Check schedule generation quota for the current ownership group."""
    from backend.services.billing import check_schedule_quota

    return await check_schedule_quota(db, str(current_user.company_id))


@router.post("/generate")
async def generate_schedule(
    body: GenerateRequest,
    request: Request,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    from backend.scheduling.graph import run_scheduling_pipeline
    from backend.services.plan import check_can_generate

    await check_can_generate(
        db, str(current_user.company_id), use_local=body.use_local
    )

    # Pre-generation schedule quota check (both AI and local modes)
    from backend.services.billing import check_schedule_quota

    quota_status = await check_schedule_quota(db, str(current_user.company_id))
    if not quota_status["can_generate"]:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail="Schedule quota exhausted. Please purchase additional credits to continue.",
        )

    # Pre-generation credit check for AI mode
    if not body.use_local:
        from backend.config import settings as _settings_cap
        from backend.services.billing import check_ai_credits, get_og_anthropic_spend_24h, get_ownership_group_id

        credit_status = await check_ai_credits(db, str(current_user.company_id))
        if not credit_status["can_generate"]:
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail="AI credits exhausted. Please purchase additional credits to continue.",
            )

        # Per-OG daily Anthropic cost circuit breaker (Tier 2E). Independent
        # of the credit system — covers the runaway-loop / pricing-config-bug
        # scenarios where credits never deplete. Skipped when the OG isn't
        # set yet (single-Company demo state) since the daily aggregate is
        # keyed by OG.
        og_id_for_cap = await get_ownership_group_id(db, str(current_user.company_id))
        if og_id_for_cap:
            spend_24h = await get_og_anthropic_spend_24h(db, og_id_for_cap)
            if spend_24h >= _settings_cap.OG_ANTHROPIC_DAILY_CAP_USD:
                from datetime import datetime as _dt, timedelta as _td, timezone as _tz

                resets_at = _dt.now(_tz.utc) + _td(hours=24)
                raise HTTPException(
                    status_code=status.HTTP_402_PAYMENT_REQUIRED,
                    detail={
                        "code": "daily_cost_cap_exceeded",
                        "message": (
                            f"Daily Anthropic spend cap of "
                            f"${_settings_cap.OG_ANTHROPIC_DAILY_CAP_USD:.2f} reached for "
                            f"this account. Resets within 24 hours."
                        ),
                        "spend_24h_usd": spend_24h,
                        "cap_usd": _settings_cap.OG_ANTHROPIC_DAILY_CAP_USD,
                        "resets_at": resets_at.isoformat(),
                    },
                )

        # Per-Company burst cap on AI-mode generation. The schedule_lock
        # prevents *concurrent* runs but not sequential trigger-spam after
        # each release — this is the counter that stops that. Local mode
        # is exempt (sub-second compute, no Anthropic cost).
        from backend.services.rate_limit import schedule_generate_ai_limiter

        if not schedule_generate_ai_limiter.check_and_record(
            str(current_user.company_id)
        ):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={
                    "code": "schedule_burst_limit",
                    "message": (
                        "Too many AI schedule generations this hour. "
                        "Try again later or switch to local mode."
                    ),
                    "retry_after_seconds": 3600,
                },
            )

    template_ids = (
        [str(tid) for tid in body.template_ids] if body.template_ids else None
    )

    # Acquire per-Company schedule lock before starting the stream.
    # TTL is scoped to the mode: local mode is sub-second computation,
    # AI mode may take up to ~90s per Anthropic call.
    from backend.config import settings as _settings
    lock_ttl = (
        _settings.SCHEDULE_LOCK_TTL_LOCAL_GENERATE_SECONDS
        if body.use_local
        else _settings.SCHEDULE_LOCK_TTL_AI_GENERATE_SECONDS
    )
    try:
        lock = await acquire_lock(
            db,
            company_id=str(current_user.company_id),
            user_id=str(current_user.id),
            operation="generate",
            ttl_seconds=lock_ttl,
        )
    except LockHeld as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "schedule_locked",
                "locked_by": e.locked_by_full_name,
                "expires_at": e.expires_at.isoformat(),
            },
        )

    async def event_stream():
        try:
            # Record consent for AI data processing
            if not body.use_local:
                client_ip = mask_ip(request.client.host) if request.client else None
                db.add(UserConsent(
                    user_id=current_user.id,
                    company_id=current_user.company_id,
                    consent_type="data_processing_ai",
                    version="1.0",
                    ip_address=client_ip,
                ))
                await db.flush()

            try:
                async for chunk in run_scheduling_pipeline(
                    company_id=str(current_user.company_id),
                    week_start_date=str(body.week_start_date),
                    db=db,
                    template_ids=template_ids,
                    use_local=body.use_local,
                    strategy=body.strategy,
                    strategy_param=body.strategy_param if body.strategy_param is not None else 0.5,
                    strategy_param2=body.strategy_param2 if body.strategy_param2 is not None else 0.0,
                    num_days=body.num_days,
                ):
                    # Persist a ShiftSchedule row so approve/reject have a record to find
                    loc_id = chunk.get("location_id", "")
                    if loc_id:
                        sched = ShiftSchedule(
                            company_id=current_user.company_id,
                            location_id=loc_id,
                            week_start_date=body.week_start_date,
                            status="draft",
                            raw_llm_output=json.dumps(chunk.get("shifts", [])),
                            strategy=body.strategy if body.use_local else "ai",
                            strategy_param=body.strategy_param,
                            strategy_param2=body.strategy_param2,
                        )
                        db.add(sched)
                        await db.flush()
                        chunk["schedule_id"] = str(sched.id)

                        # Deduct credits if over schedule free tier
                        from backend.services.billing import deduct_credits_for_schedule_overage
                        await deduct_credits_for_schedule_overage(db, str(current_user.company_id))

                        await db.commit()

                    yield json.dumps(chunk) + "\n"
            except Exception as exc:
                from backend.services.failure_logger import log_failure

                await log_failure(
                    category="PIPELINE",
                    source="schedules.generate",
                    message=str(exc),
                    detail={
                        "week_start_date": str(body.week_start_date),
                        "exception_type": type(exc).__name__,
                    },
                    company_id=current_user.company_id,
                )
                # Emit the error as a single NDJSON line so the frontend can display it
                error_result = {
                    "location_id": "",
                    "location_name": "Pipeline Error",
                    "shifts": [],
                    "errors": [str(exc)],
                    "status": "PIPELINE_ERROR",
                }
                yield json.dumps(error_result) + "\n"
        finally:
            try:
                await release_lock(db, lock.id)
                await db.commit()
            except Exception:
                logger.exception(
                    "Failed to release schedule lock for company=%s",
                    current_user.company_id,
                )

    return StreamingResponse(event_stream(), media_type="application/x-ndjson")


@router.put("/{schedule_id}/shifts")
async def update_shifts(
    schedule_id: str,
    body: UpdateShiftsRequest,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await db.execute(
        select(ShiftSchedule).where(
            ShiftSchedule.id == schedule_id,
            ShiftSchedule.company_id == current_user.company_id,
        )
    )
    schedule = result.scalar_one_or_none()
    if schedule is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Schedule not found")

    if schedule.status == "approved":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot edit an approved schedule")

    schedule.raw_llm_output = json.dumps([s.model_dump() for s in body.shifts])
    await db.commit()
    return {"ok": True}


@router.post("/{schedule_id}/approve", response_model=ShiftScheduleResponse)
async def approve_schedule(
    schedule_id: str,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> ShiftScheduleResponse:
    result = await db.execute(
        select(ShiftSchedule).where(
            ShiftSchedule.id == schedule_id,
            ShiftSchedule.company_id == current_user.company_id,
        )
    )
    schedule = result.scalar_one_or_none()
    if schedule is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Schedule not found")

    if schedule.status == "approved":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Schedule already approved")

    try:
        lock = await acquire_lock(
            db,
            company_id=str(current_user.company_id),
            user_id=str(current_user.id),
            operation="approve",
        )
    except LockHeld as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "schedule_locked",
                "locked_by": e.locked_by_full_name,
                "expires_at": e.expires_at.isoformat(),
            },
        )

    try:
        schedule.status = "approved"

        # Create Shift records from the stored raw_llm_output
        if schedule.raw_llm_output:
            try:
                # Build set of valid role IDs and condensed-role -> member-role mapping
                from backend.models import Role
                from backend.models.condensed_role import CondensedRoleMapping
                role_result = await db.execute(
                    select(Role.id).where(Role.company_id == current_user.company_id)
                )
                valid_role_ids: set[str] = {str(r) for r in role_result.scalars().all()}

                crm_result = await db.execute(
                    select(CondensedRoleMapping).join(
                        CondensedRoleMapping.condensed_role
                    ).where(
                        CondensedRoleMapping.condensed_role.has(company_id=current_user.company_id)
                    )
                )
                # Map condensed_role_id -> first member role_id
                condensed_to_role: dict[str, str] = {}
                for crm in crm_result.scalars().all():
                    cid = str(crm.condensed_role_id)
                    if cid not in condensed_to_role:
                        condensed_to_role[cid] = str(crm.role_id)

                shifts_data = json.loads(schedule.raw_llm_output)
                for s in shifts_data:
                    # Skip VACANT placeholders and non-ok shifts
                    if s.get("status") != "ok" or s.get("employee_id") == "VACANT":
                        continue
                    # Skip shifts with missing required IDs
                    try:
                        loc_id = s["location_id"]
                        emp_id = s["employee_id"]
                        role_id = s["role_id"]
                    except KeyError:
                        continue
                    if not role_id:
                        continue
                    # Resolve condensed role IDs to a real role ID
                    if role_id not in valid_role_ids:
                        role_id = condensed_to_role.get(role_id, role_id)
                    if role_id not in valid_role_ids:
                        logger.warning(
                            "Skipping shift with unknown role_id %s (role_name=%s)",
                            role_id, s.get("role_name", ""),
                        )
                        continue
                    shift = Shift(
                        company_id=current_user.company_id,
                        shift_schedule_id=schedule.id,
                        location_id=loc_id,
                        employee_id=emp_id,
                        role_id=role_id,
                        role_name=s.get("role_name", ""),
                        date=date.fromisoformat(s["date"]) if isinstance(s["date"], str) else s["date"],
                        start_time=datetime.fromisoformat(s["start_time"]) if isinstance(s["start_time"], str) else s["start_time"],
                        end_time=datetime.fromisoformat(s["end_time"]) if isinstance(s["end_time"], str) else s["end_time"],
                    )
                    db.add(shift)
            except (json.JSONDecodeError, KeyError, ValueError) as exc:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Failed to parse schedule data: {exc}",
                )

            # Subtract consumed hours from employee availability.
            # For each approved shift, find the overlapping availability window
            # and split it around the shift (removing only the scheduled hours).
            await _subtract_availability_for_shifts(
                db, current_user.company_id, shifts_data,
            )

            # Accumulate worked minutes into employee_role_minutes for history tracking
            from backend.models.employee_role_minutes import EmployeeRoleMinutes

            for s in shifts_data:
                if s.get("status") != "ok" or s.get("employee_id") == "VACANT":
                    continue
                try:
                    emp_id = s["employee_id"]
                    role_id_val = s["role_id"]
                    shift_start = datetime.fromisoformat(s["start_time"])
                    shift_end = datetime.fromisoformat(s["end_time"])
                    minutes = (shift_end - shift_start).total_seconds() / 60.0
                    shift_date = date.fromisoformat(s["date"]) if isinstance(s["date"], str) else s["date"]
                    month_start = shift_date.replace(day=1)

                    # Resolve condensed role IDs to a real role ID
                    if role_id_val not in valid_role_ids:
                        role_id_val = condensed_to_role.get(role_id_val, role_id_val)
                    if role_id_val not in valid_role_ids:
                        continue

                    existing = await db.execute(
                        select(EmployeeRoleMinutes).where(
                            EmployeeRoleMinutes.company_id == current_user.company_id,
                            EmployeeRoleMinutes.employee_id == emp_id,
                            EmployeeRoleMinutes.role_id == role_id_val,
                            EmployeeRoleMinutes.month_start == month_start,
                        )
                    )
                    record = existing.scalar_one_or_none()
                    if record:
                        record.total_minutes += minutes
                    else:
                        db.add(EmployeeRoleMinutes(
                            company_id=current_user.company_id,
                            employee_id=emp_id,
                            role_id=role_id_val,
                            month_start=month_start,
                            total_minutes=minutes,
                        ))
                except (KeyError, ValueError):
                    continue

        await db.commit()
        await db.refresh(schedule)

        # Fetch shifts for response
        shift_result = await db.execute(
            select(Shift).where(Shift.shift_schedule_id == schedule.id)
        )
        shifts = shift_result.scalars().all()

        resp = ShiftScheduleResponse.model_validate(schedule)
        resp.shifts = [_shift_to_response(s) for s in shifts]
    except Exception:
        await db.rollback()
        raise
    finally:
        await release_lock(db, lock.id)
        await db.commit()

    return resp


@router.post("/{schedule_id}/reject", response_model=ShiftScheduleResponse)
async def reject_schedule(
    schedule_id: str,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> ShiftScheduleResponse:
    result = await db.execute(
        select(ShiftSchedule).where(
            ShiftSchedule.id == schedule_id,
            ShiftSchedule.company_id == current_user.company_id,
        )
    )
    schedule = result.scalar_one_or_none()
    if schedule is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Schedule not found")

    schedule.status = "rejected"
    await db.commit()
    await db.refresh(schedule)
    return ShiftScheduleResponse.model_validate(schedule)


@router.get("/week/{week_start_date}", response_model=list[ShiftScheduleResponse])
async def get_week_schedules(
    week_start_date: date,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> list[ShiftScheduleResponse]:
    result = await db.execute(
        select(ShiftSchedule).where(
            ShiftSchedule.company_id == current_user.company_id,
            ShiftSchedule.week_start_date == week_start_date,
        )
    )
    schedules = result.scalars().all()

    responses: list[ShiftScheduleResponse] = []
    for sched in schedules:
        shift_result = await db.execute(
            select(Shift).where(Shift.shift_schedule_id == sched.id)
        )
        shifts = shift_result.scalars().all()
        resp = ShiftScheduleResponse.model_validate(sched)
        resp.shifts = [_shift_to_response(s) for s in shifts]
        responses.append(resp)

    return responses


def _shift_to_response(shift: Shift) -> dict:
    """Convert a Shift ORM object to a dict matching ShiftResponse."""
    from backend.schemas.schedule import ShiftResponse

    return ShiftResponse.model_validate(shift)
