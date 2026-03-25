import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.dependencies import get_db, require_manager
from backend.models import Employee, EmployeeAffinity, User
from backend.schemas.employee import (
    EmployeeAffinityCreate,
    EmployeeAffinityResponse,
    EmployeeAffinityUpdate,
)

router = APIRouter(prefix="/affinities", tags=["affinities"])


@router.get("/", response_model=list[EmployeeAffinityResponse])
async def list_affinities(
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> list[EmployeeAffinityResponse]:
    result = await db.execute(
        select(EmployeeAffinity).where(
            EmployeeAffinity.company_id == current_user.company_id
        )
    )
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
) -> EmployeeAffinityResponse:
    # Verify both employees belong to this company
    for eid in (body.employee_id, body.target_employee_id):
        result = await db.execute(
            select(Employee).where(
                Employee.id == eid,
                Employee.company_id == current_user.company_id,
            )
        )
        if result.scalar_one_or_none() is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Employee {eid} not found",
            )

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
    affinity_id: uuid.UUID,
    body: EmployeeAffinityUpdate,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
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

    if body.target_employee_id is not None:
        # Verify target employee belongs to company
        emp_result = await db.execute(
            select(Employee).where(
                Employee.id == body.target_employee_id,
                Employee.company_id == current_user.company_id,
            )
        )
        if emp_result.scalar_one_or_none() is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Employee {body.target_employee_id} not found",
            )
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
    affinity_id: uuid.UUID,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
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
    await db.delete(affinity)
    await db.commit()
