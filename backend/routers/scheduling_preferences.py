"""CRUD for the three weighted per-employee scheduling preferences.

Deliberately ungated: unlike AI generation and the 7shifts/Deputy importers,
these endpoints must NOT call assert_paid_plan. The deterministic scheduler
is the free tier's product and these preferences improve it, so gating them
would weaken the tier they most help.

Every handler filters by current_user.company_id (multi-tenancy), and
creating/updating always re-verifies the target employee belongs to that
company before writing.

Location scope (#149): reads list only rows for employees the manager can
see; every write needs the employee editable (spec Q2), else 404.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.dependencies import get_db, get_location_scope, require_manager
from backend.models import (
    Employee,
    EmployeeDayPreference,
    EmployeeHourRangeCap,
    EmployeeHourRangePreference,
    User,
)
from backend.routers.employees import (
    require_employee_editable,
    require_employee_id_editable,
    visible_employee_ids,
)
from backend.services.archive import active
from backend.services.location_scope import LocationScope
from backend.schemas.employee import (
    EmployeeDayPreferenceCreate,
    EmployeeDayPreferenceResponse,
    EmployeeDayPreferenceUpdate,
    EmployeeHourRangeCapCreate,
    EmployeeHourRangeCapResponse,
    EmployeeHourRangeCapUpdate,
    EmployeeHourRangePreferenceCreate,
    EmployeeHourRangePreferenceResponse,
    EmployeeHourRangePreferenceUpdate,
)

router = APIRouter(prefix="/scheduling-preferences", tags=["scheduling-preferences"])


async def _get_owned_employee(
    db: AsyncSession, employee_id: str, company_id: str
) -> Employee | None:
    result = await db.execute(
        select(Employee).where(
            Employee.id == employee_id, Employee.company_id == company_id,
            active(Employee),
        )
    )
    return result.scalar_one_or_none()


# ── Day preferences ──


@router.get("/days", response_model=list[EmployeeDayPreferenceResponse])
async def list_day_preferences(
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
    scope: LocationScope = Depends(get_location_scope),
) -> list[EmployeeDayPreferenceResponse]:
    query = select(EmployeeDayPreference).where(
        EmployeeDayPreference.company_id == current_user.company_id
    )
    visible = await visible_employee_ids(db, current_user.company_id, scope)
    if visible is not None:
        query = query.where(EmployeeDayPreference.employee_id.in_(sorted(visible)))
    result = await db.execute(query)
    return [
        EmployeeDayPreferenceResponse.model_validate(r)
        for r in result.scalars().all()
    ]


@router.post(
    "/days",
    response_model=EmployeeDayPreferenceResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_day_preference(
    body: EmployeeDayPreferenceCreate,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
    scope: LocationScope = Depends(get_location_scope),
) -> EmployeeDayPreferenceResponse:
    employee = await _get_owned_employee(db, body.employee_id, current_user.company_id)
    if employee is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Employee not found"
        )
    require_employee_editable(scope, employee)

    row = EmployeeDayPreference(
        company_id=current_user.company_id,
        employee_id=body.employee_id,
        day_of_week=body.day_of_week,
        weight=body.weight,
    )
    db.add(row)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A day preference for this employee and day already exists",
        )
    await db.refresh(row)
    return EmployeeDayPreferenceResponse.model_validate(row)


@router.put("/days/{pref_id}", response_model=EmployeeDayPreferenceResponse)
async def update_day_preference(
    pref_id: str,
    body: EmployeeDayPreferenceUpdate,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
    scope: LocationScope = Depends(get_location_scope),
) -> EmployeeDayPreferenceResponse:
    result = await db.execute(
        select(EmployeeDayPreference).where(
            EmployeeDayPreference.id == pref_id,
            EmployeeDayPreference.company_id == current_user.company_id,
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Day preference not found"
        )
    await require_employee_id_editable(
        db, scope, current_user.company_id, row.employee_id, "Day preference",
    )

    if body.day_of_week is not None:
        row.day_of_week = body.day_of_week
    if body.weight is not None:
        row.weight = body.weight

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A day preference for this employee and day already exists",
        )
    await db.refresh(row)
    return EmployeeDayPreferenceResponse.model_validate(row)


@router.delete("/days/{pref_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_day_preference(
    pref_id: str,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
    scope: LocationScope = Depends(get_location_scope),
) -> None:
    result = await db.execute(
        select(EmployeeDayPreference).where(
            EmployeeDayPreference.id == pref_id,
            EmployeeDayPreference.company_id == current_user.company_id,
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Day preference not found"
        )
    await require_employee_id_editable(
        db, scope, current_user.company_id, row.employee_id, "Day preference",
    )
    await db.delete(row)
    await db.commit()


# ── Hour-range preferences ──


@router.get("/hour-ranges", response_model=list[EmployeeHourRangePreferenceResponse])
async def list_hour_range_preferences(
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
    scope: LocationScope = Depends(get_location_scope),
) -> list[EmployeeHourRangePreferenceResponse]:
    query = select(EmployeeHourRangePreference).where(
        EmployeeHourRangePreference.company_id == current_user.company_id
    )
    visible = await visible_employee_ids(db, current_user.company_id, scope)
    if visible is not None:
        query = query.where(EmployeeHourRangePreference.employee_id.in_(sorted(visible)))
    result = await db.execute(query)
    return [
        EmployeeHourRangePreferenceResponse.model_validate(r)
        for r in result.scalars().all()
    ]


@router.post(
    "/hour-ranges",
    response_model=EmployeeHourRangePreferenceResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_hour_range_preference(
    body: EmployeeHourRangePreferenceCreate,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
    scope: LocationScope = Depends(get_location_scope),
) -> EmployeeHourRangePreferenceResponse:
    employee = await _get_owned_employee(db, body.employee_id, current_user.company_id)
    if employee is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Employee not found"
        )
    require_employee_editable(scope, employee)

    row = EmployeeHourRangePreference(
        company_id=current_user.company_id,
        employee_id=body.employee_id,
        start_time=body.start_time,
        end_time=body.end_time,
        weight=body.weight,
    )
    db.add(row)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An hour-range preference for this employee and range already exists",
        )
    await db.refresh(row)
    return EmployeeHourRangePreferenceResponse.model_validate(row)


@router.put(
    "/hour-ranges/{pref_id}", response_model=EmployeeHourRangePreferenceResponse
)
async def update_hour_range_preference(
    pref_id: str,
    body: EmployeeHourRangePreferenceUpdate,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
    scope: LocationScope = Depends(get_location_scope),
) -> EmployeeHourRangePreferenceResponse:
    result = await db.execute(
        select(EmployeeHourRangePreference).where(
            EmployeeHourRangePreference.id == pref_id,
            EmployeeHourRangePreference.company_id == current_user.company_id,
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Hour-range preference not found",
        )
    await require_employee_id_editable(
        db, scope, current_user.company_id, row.employee_id, "Hour-range preference",
    )

    new_start = body.start_time if body.start_time is not None else row.start_time
    new_end = body.end_time if body.end_time is not None else row.end_time
    if new_start == new_end:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="start_time and end_time must not be equal",
        )

    if body.start_time is not None:
        row.start_time = body.start_time
    if body.end_time is not None:
        row.end_time = body.end_time
    if body.weight is not None:
        row.weight = body.weight

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An hour-range preference for this employee and range already exists",
        )
    await db.refresh(row)
    return EmployeeHourRangePreferenceResponse.model_validate(row)


@router.delete("/hour-ranges/{pref_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_hour_range_preference(
    pref_id: str,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
    scope: LocationScope = Depends(get_location_scope),
) -> None:
    result = await db.execute(
        select(EmployeeHourRangePreference).where(
            EmployeeHourRangePreference.id == pref_id,
            EmployeeHourRangePreference.company_id == current_user.company_id,
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Hour-range preference not found",
        )
    await require_employee_id_editable(
        db, scope, current_user.company_id, row.employee_id, "Hour-range preference",
    )
    await db.delete(row)
    await db.commit()


# ── Hour-range caps ──


@router.get("/caps", response_model=list[EmployeeHourRangeCapResponse])
async def list_hour_range_caps(
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
    scope: LocationScope = Depends(get_location_scope),
) -> list[EmployeeHourRangeCapResponse]:
    query = select(EmployeeHourRangeCap).where(
        EmployeeHourRangeCap.company_id == current_user.company_id
    )
    visible = await visible_employee_ids(db, current_user.company_id, scope)
    if visible is not None:
        query = query.where(EmployeeHourRangeCap.employee_id.in_(sorted(visible)))
    result = await db.execute(query)
    return [
        EmployeeHourRangeCapResponse.model_validate(r)
        for r in result.scalars().all()
    ]


@router.post(
    "/caps",
    response_model=EmployeeHourRangeCapResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_hour_range_cap(
    body: EmployeeHourRangeCapCreate,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
    scope: LocationScope = Depends(get_location_scope),
) -> EmployeeHourRangeCapResponse:
    employee = await _get_owned_employee(db, body.employee_id, current_user.company_id)
    if employee is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Employee not found"
        )
    require_employee_editable(scope, employee)

    row = EmployeeHourRangeCap(
        company_id=current_user.company_id,
        employee_id=body.employee_id,
        start_time=body.start_time,
        end_time=body.end_time,
        max_per_week=body.max_per_week,
        weight=body.weight,
    )
    db.add(row)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An hour-range cap for this employee and range already exists",
        )
    await db.refresh(row)
    return EmployeeHourRangeCapResponse.model_validate(row)


@router.put("/caps/{cap_id}", response_model=EmployeeHourRangeCapResponse)
async def update_hour_range_cap(
    cap_id: str,
    body: EmployeeHourRangeCapUpdate,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
    scope: LocationScope = Depends(get_location_scope),
) -> EmployeeHourRangeCapResponse:
    result = await db.execute(
        select(EmployeeHourRangeCap).where(
            EmployeeHourRangeCap.id == cap_id,
            EmployeeHourRangeCap.company_id == current_user.company_id,
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Hour-range cap not found"
        )
    await require_employee_id_editable(
        db, scope, current_user.company_id, row.employee_id, "Hour-range cap",
    )

    new_start = body.start_time if body.start_time is not None else row.start_time
    new_end = body.end_time if body.end_time is not None else row.end_time
    if new_start == new_end:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="start_time and end_time must not be equal",
        )

    if body.start_time is not None:
        row.start_time = body.start_time
    if body.end_time is not None:
        row.end_time = body.end_time
    if body.max_per_week is not None:
        row.max_per_week = body.max_per_week
    if body.weight is not None:
        row.weight = body.weight

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An hour-range cap for this employee and range already exists",
        )
    await db.refresh(row)
    return EmployeeHourRangeCapResponse.model_validate(row)


@router.delete("/caps/{cap_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_hour_range_cap(
    cap_id: str,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
    scope: LocationScope = Depends(get_location_scope),
) -> None:
    result = await db.execute(
        select(EmployeeHourRangeCap).where(
            EmployeeHourRangeCap.id == cap_id,
            EmployeeHourRangeCap.company_id == current_user.company_id,
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Hour-range cap not found"
        )
    await require_employee_id_editable(
        db, scope, current_user.company_id, row.employee_id, "Hour-range cap",
    )
    await db.delete(row)
    await db.commit()
