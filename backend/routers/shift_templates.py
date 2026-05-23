from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.dependencies import get_db, require_manager
from backend.models import ShiftTemplate, User
from backend.schemas.shift_template import (
    ShiftTemplateCreate,
    ShiftTemplateResponse,
    ShiftTemplateUpdate,
)

router = APIRouter(prefix="/shift-templates", tags=["shift-templates"])


@router.get("/", response_model=list[ShiftTemplateResponse])
async def list_shift_templates(
    include_overrides: bool = False,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> list[ShiftTemplateResponse]:
    """List shift templates for the current company.

    By default returns only recurring templates (rows where ``specific_date IS
    NULL``). Override rows (specific-date clones produced by the special-hours
    flow) are not picker-eligible — the scheduler resolves them automatically
    via ``resolve_templates_for_week``, and surfacing them in the picker would
    let a manager select an override and silently have the recurring filter in
    ``_load_initial_state`` drop it.

    Pass ``include_overrides=true`` from the ShiftTemplates management page so
    cloned overrides remain visible for inspection/edit.
    """
    query = select(ShiftTemplate).where(
        ShiftTemplate.company_id == current_user.company_id
    )
    if not include_overrides:
        query = query.where(ShiftTemplate.specific_date.is_(None))
    result = await db.execute(query)
    templates = result.scalars().all()
    return [ShiftTemplateResponse.model_validate(t) for t in templates]


@router.post("/", response_model=ShiftTemplateResponse, status_code=status.HTTP_201_CREATED)
async def create_shift_template(
    body: ShiftTemplateCreate,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> ShiftTemplateResponse:
    template = ShiftTemplate(
        company_id=current_user.company_id,
        location_id=body.location_id,
        name=body.name,
        weekly_schedule=body.weekly_schedule,
    )
    db.add(template)
    await db.commit()
    await db.refresh(template)
    return ShiftTemplateResponse.model_validate(template)


@router.put("/{template_id}", response_model=ShiftTemplateResponse)
async def update_shift_template(
    template_id: str,
    body: ShiftTemplateUpdate,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> ShiftTemplateResponse:
    result = await db.execute(
        select(ShiftTemplate).where(
            ShiftTemplate.id == template_id,
            ShiftTemplate.company_id == current_user.company_id,
        )
    )
    template = result.scalar_one_or_none()
    if template is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shift template not found")

    if body.name is not None:
        template.name = body.name
    if body.weekly_schedule is not None:
        template.weekly_schedule = body.weekly_schedule

    await db.commit()
    await db.refresh(template)
    return ShiftTemplateResponse.model_validate(template)


@router.delete("/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_shift_template(
    template_id: str,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> None:
    result = await db.execute(
        select(ShiftTemplate).where(
            ShiftTemplate.id == template_id,
            ShiftTemplate.company_id == current_user.company_id,
        )
    )
    template = result.scalar_one_or_none()
    if template is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shift template not found")

    await db.delete(template)
    await db.commit()
