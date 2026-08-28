import json
import logging
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.dependencies import get_db, require_manager
from backend.models import Employee, EmployeeCheckIn, Shift, ShiftSchedule, User
from backend.models.consent import UserConsent
from backend.utils.privacy import mask_ip
from backend.schemas.schedule import (
    EditApprovedResponse,
    EditApprovedShiftsRequest,
    GenerateRequest,
    ShiftScheduleResponse,
    UpdateShiftsRequest,
)
from backend.services.schedule_lock import LockHeld, acquire as acquire_lock, release as release_lock

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/schedules", tags=["schedules"])


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


@router.put("/{schedule_id}/approved-shifts", response_model=EditApprovedResponse)
async def edit_approved_shifts(
    schedule_id: str,
    body: EditApprovedShiftsRequest,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> EditApprovedResponse:
    """Edit an approved schedule's shifts.

    Writes to the `shifts` table, not raw_llm_output: after approval every
    consumer reads the table (export_schedules.py, gdpr.py, check-ins), so
    editing the blob would be a silent no-op.
    """
    schedule = (await db.execute(
        select(ShiftSchedule).where(
            ShiftSchedule.id == schedule_id,
            ShiftSchedule.company_id == current_user.company_id,
        )
    )).scalar_one_or_none()
    if schedule is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Schedule not found")

    if schedule.status != "approved":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "not_approved", "message": "This endpoint edits approved schedules only."},
        )

    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.APPROVED_SCHEDULE_EDIT_DAYS)
    created_at = schedule.created_at
    if created_at.tzinfo is None:
        # SQLite (the test DB) returns DateTime(timezone=True) columns as
        # naive; Postgres always returns aware. created_at is written in UTC
        # either way, so tag it rather than convert it.
        created_at = created_at.replace(tzinfo=timezone.utc)
    if created_at < cutoff:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "edit_window_closed",
                "message": (
                    f"Approved schedules can be edited for "
                    f"{settings.APPROVED_SCHEDULE_EDIT_DAYS} days after approval."
                ),
            },
        )

    touched_ids = [e.shift_id for e in body.edits if e.shift_id]
    if touched_ids:
        locked = (await db.execute(
            select(EmployeeCheckIn.shift_id).where(
                EmployeeCheckIn.company_id == current_user.company_id,
                EmployeeCheckIn.shift_id.in_(touched_ids),
            )
        )).scalars().all()
        if locked:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "shift_locked_by_checkin",
                    "message": "An employee has already checked in against this shift.",
                    "shift_ids": [str(s) for s in locked],
                },
            )

    try:
        lock = await acquire_lock(
            db,
            company_id=str(current_user.company_id),
            user_id=str(current_user.id),
            operation="edit_approved",
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
        from backend.models import Role

        applied = 0
        for idx, edit in enumerate(body.edits):
            if edit.employee_id is not None:
                # Every employee referenced must belong to the caller's
                # company — otherwise a manager could schedule another
                # tenant's staff.
                employee = (await db.execute(
                    select(Employee.id).where(
                        Employee.id == edit.employee_id,
                        Employee.company_id == current_user.company_id,
                    )
                )).scalar_one_or_none()
                if employee is None:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail="Employee not found",
                    )

            if edit.shift_id:
                shift = (await db.execute(
                    select(Shift).where(
                        Shift.id == edit.shift_id,
                        Shift.company_id == current_user.company_id,
                    )
                )).scalar_one_or_none()
                if shift is None:
                    # All-or-nothing: an edit list is a single manager
                    # decision. Silently skipping one edit while committing
                    # the rest would leave the manager believing the whole
                    # batch applied, so any unresolvable edit fails (and
                    # rolls back) the entire request instead.
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail={
                            "code": "invalid_edit",
                            "index": idx,
                            "shift_id": edit.shift_id,
                            "reason": "shift not found",
                        },
                    )
                if edit.deleted:
                    await db.delete(shift)
                    applied += 1
                    continue
                changed = False
                if edit.employee_id is not None:
                    shift.employee_id = edit.employee_id
                    changed = True
                if edit.role_id is not None:
                    # Same company check as the employee check above — a
                    # manager must not be able to point a shift at another
                    # tenant's role. role_name is denormalised onto Shift
                    # (read by the UI and the 7shifts export), so it has to
                    # be refreshed here too or it goes stale next to the new
                    # role_id.
                    role_name = (await db.execute(
                        select(Role.name).where(
                            Role.id == edit.role_id,
                            Role.company_id == current_user.company_id,
                        )
                    )).scalar_one_or_none()
                    if role_name is None:
                        raise HTTPException(
                            status_code=status.HTTP_404_NOT_FOUND,
                            detail="Role not found",
                        )
                    shift.role_id = edit.role_id
                    shift.role_name = role_name
                    changed = True
                if edit.date is not None:
                    shift.date = edit.date
                    changed = True
                if edit.start_time is not None:
                    shift.start_time = edit.start_time
                    changed = True
                if edit.end_time is not None:
                    shift.end_time = edit.end_time
                    changed = True
                if changed:
                    applied += 1
            else:
                role_name = (await db.execute(
                    select(Role.name).where(
                        Role.id == edit.role_id,
                        Role.company_id == current_user.company_id,
                    )
                )).scalar_one_or_none()
                if role_name is None:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail={
                            "code": "invalid_edit",
                            "index": idx,
                            "shift_id": None,
                            "reason": "role not found",
                        },
                    )
                db.add(Shift(
                    company_id=current_user.company_id,
                    shift_schedule_id=schedule.id,
                    location_id=schedule.location_id,
                    employee_id=edit.employee_id,
                    role_id=edit.role_id,
                    role_name=role_name,
                    date=edit.date,
                    start_time=edit.start_time,
                    end_time=edit.end_time,
                ))
                applied += 1

        await db.commit()
        result = EditApprovedResponse(applied=applied, warnings=[])
    except Exception:
        await db.rollback()
        raise
    finally:
        await release_lock(db, lock.id)
        await db.commit()

    return result


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
    status_filter: str | None = Query(None, alias="status"),
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> list[ShiftScheduleResponse]:
    """Schedules for one week, newest-relevant first.

    `?status=approved` narrows to approved schedules. Without it every
    generation that was never approved comes back too — a week can easily
    hold dozens of empty drafts before RETENTION_STALE_DRAFTS_DAYS clears
    them, and callers that only render approved schedules should not pay
    for that.
    """
    conditions = [
        ShiftSchedule.company_id == current_user.company_id,
        ShiftSchedule.week_start_date == week_start_date,
    ]
    if status_filter:
        conditions.append(ShiftSchedule.status == status_filter)

    result = await db.execute(select(ShiftSchedule).where(*conditions))
    schedules = result.scalars().all()

    # Resolve employee names once for the whole week rather than per shift.
    # Same approach as export_schedules.py, which needs the identical map.
    emp_rows = (await db.execute(
        select(Employee).where(Employee.company_id == current_user.company_id)
    )).scalars().all()
    emp_names = {e.id: (e.full_name or str(e.id)) for e in emp_rows}

    responses: list[ShiftScheduleResponse] = []
    for sched in schedules:
        shift_result = await db.execute(
            select(Shift).where(Shift.shift_schedule_id == sched.id)
        )
        shifts = shift_result.scalars().all()
        resp = ShiftScheduleResponse.model_validate(sched)
        resp.shifts = [_shift_to_response(s, emp_names) for s in shifts]
        responses.append(resp)

    return responses


def _shift_to_response(shift: Shift, emp_names: dict[str, str] | None = None) -> dict:
    """Convert a Shift ORM object to a dict matching ShiftResponse.

    employee_name is not a column on Shift, so callers that render names pass
    a resolved employee_id -> full_name map.
    """
    from backend.schemas.schedule import ShiftResponse

    resp = ShiftResponse.model_validate(shift)
    if emp_names:
        resp.employee_name = emp_names.get(shift.employee_id, "")
    return resp
