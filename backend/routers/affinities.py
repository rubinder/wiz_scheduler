from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.dependencies import get_db, get_location_scope, require_manager
from backend.models import Employee, EmployeeAffinity, User
from backend.services.location_scope import (
    require_employee_editable,
    require_employee_id_editable,
    visible_employee_ids,
)
from backend.services.archive import active
from backend.services.location_scope import LocationScope
from backend.schemas.employee import (
    EmployeeAffinityCreate,
    EmployeeAffinityResponse,
    EmployeeAffinityUpdate,
)

router = APIRouter(prefix="/affinities", tags=["affinities"])

# Location scope (#149, spec Q2): an affinity involves two employees, so a
# manager sees it only when both are visible and changes it only when both
# are editable. Out of scope answers 404 like another company's row.


@router.get("/", response_model=list[EmployeeAffinityResponse])
async def list_affinities(
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
    scope: LocationScope = Depends(get_location_scope),
) -> list[EmployeeAffinityResponse]:
    query = select(EmployeeAffinity).where(
        EmployeeAffinity.company_id == current_user.company_id
    )
    visible = await visible_employee_ids(db, current_user.company_id, scope)
    if visible is not None:
        ids = sorted(visible)
        query = query.where(
            EmployeeAffinity.employee_id.in_(ids),
            EmployeeAffinity.target_employee_id.in_(ids),
        )
    result = await db.execute(query)
    return [
        EmployeeAffinityResponse.model_validate(a) for a in result.scalars().all()
    ]


@router.post(
    "/", response_model=EmployeeAffinityResponse, status_code=status.HTTP_201_CREATED
)
async def create_affinity(
    body: EmployeeAffinityCreate,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
    scope: LocationScope = Depends(get_location_scope),
) -> EmployeeAffinityResponse:
    # Verify both employees belong to this company
    for eid in (body.employee_id, body.target_employee_id):
        result = await db.execute(
            select(Employee).where(
                Employee.id == eid,
                Employee.company_id == current_user.company_id,
                active(Employee),
            )
        )
        employee = result.scalar_one_or_none()
        if employee is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Employee {eid} not found",
            )
        require_employee_editable(scope, employee, f"Employee {eid}")

    affinity = EmployeeAffinity(
        company_id=current_user.company_id,
        employee_id=body.employee_id,
        target_employee_id=body.target_employee_id,
        level=body.level,
        entry_date=body.entry_date,
        expiration_date=body.expiration_date,
    )
    db.add(affinity)
    await db.commit()
    await db.refresh(affinity)
    return EmployeeAffinityResponse.model_validate(affinity)


@router.put("/{affinity_id}", response_model=EmployeeAffinityResponse)
async def update_affinity(
    affinity_id: str,
    body: EmployeeAffinityUpdate,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
    scope: LocationScope = Depends(get_location_scope),
) -> EmployeeAffinityResponse:
    result = await db.execute(
        select(EmployeeAffinity).where(
            EmployeeAffinity.id == affinity_id,
            EmployeeAffinity.company_id == current_user.company_id,
        )
    )
    affinity = result.scalar_one_or_none()
    if affinity is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Affinity not found"
        )
    for eid in (affinity.employee_id, affinity.target_employee_id):
        await require_employee_id_editable(
            db, scope, current_user.company_id, eid, "Affinity",
        )

    if body.target_employee_id is not None:
        # Verify target employee belongs to company
        emp_result = await db.execute(
            select(Employee).where(
                Employee.id == body.target_employee_id,
                Employee.company_id == current_user.company_id,
                active(Employee),
            )
        )
        target = emp_result.scalar_one_or_none()
        if target is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Employee {body.target_employee_id} not found",
            )
        require_employee_editable(scope, target, f"Employee {body.target_employee_id}")
        affinity.target_employee_id = body.target_employee_id

    if body.level is not None:
        affinity.level = body.level
    if body.entry_date is not None:
        affinity.entry_date = body.entry_date
    if body.expiration_date is not None:
        affinity.expiration_date = body.expiration_date

    await db.commit()
    await db.refresh(affinity)
    return EmployeeAffinityResponse.model_validate(affinity)


@router.delete("/{affinity_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_affinity(
    affinity_id: str,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
    scope: LocationScope = Depends(get_location_scope),
) -> None:
    result = await db.execute(
        select(EmployeeAffinity).where(
            EmployeeAffinity.id == affinity_id,
            EmployeeAffinity.company_id == current_user.company_id,
        )
    )
    affinity = result.scalar_one_or_none()
    if affinity is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Affinity not found"
        )
    for eid in (affinity.employee_id, affinity.target_employee_id):
        await require_employee_id_editable(
            db, scope, current_user.company_id, eid, "Affinity",
        )
    await db.delete(affinity)
    await db.commit()
