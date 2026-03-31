from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.dependencies import get_db, require_manager
from backend.models import Role, User
from backend.models.condensed_role import CondensedRole, CondensedRoleMapping
from backend.schemas.condensed_role import (
    CondensedRoleCreate,
    CondensedRoleMappingResponse,
    CondensedRoleResponse,
    CondensedRoleUpdate,
)

router = APIRouter(prefix="/condensed-roles", tags=["condensed-roles"])


async def _build_response(
    cr: CondensedRole, db: AsyncSession
) -> CondensedRoleResponse:
    """Build a CondensedRoleResponse with role names resolved."""
    role_ids = [m.role_id for m in cr.mappings]
    role_map: dict[str, str] = {}
    if role_ids:
        result = await db.execute(select(Role).where(Role.id.in_(role_ids)))
        role_map = {r.id: r.name for r in result.scalars().all()}

    return CondensedRoleResponse(
        id=cr.id,
        company_id=cr.company_id,
        name=cr.name,
        roles=[
            CondensedRoleMappingResponse(
                role_id=m.role_id,
                role_name=role_map.get(m.role_id, ""),
            )
            for m in cr.mappings
        ],
    )


@router.get("/", response_model=list[CondensedRoleResponse])
async def list_condensed_roles(
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> list[CondensedRoleResponse]:
    result = await db.execute(
        select(CondensedRole).where(
            CondensedRole.company_id == current_user.company_id
        )
    )
    crs = result.scalars().all()
    return [await _build_response(cr, db) for cr in crs]


@router.post("/", response_model=CondensedRoleResponse, status_code=status.HTTP_201_CREATED)
async def create_condensed_role(
    body: CondensedRoleCreate,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> CondensedRoleResponse:
    cr = CondensedRole(
        company_id=current_user.company_id,
        name=body.name,
    )
    db.add(cr)
    await db.flush()

    for role_id in body.role_ids:
        db.add(CondensedRoleMapping(condensed_role_id=cr.id, role_id=role_id))

    await db.commit()
    await db.refresh(cr)
    return await _build_response(cr, db)


@router.put("/{condensed_role_id}", response_model=CondensedRoleResponse)
async def update_condensed_role(
    condensed_role_id: str,
    body: CondensedRoleUpdate,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> CondensedRoleResponse:
    result = await db.execute(
        select(CondensedRole).where(
            CondensedRole.id == condensed_role_id,
            CondensedRole.company_id == current_user.company_id,
        )
    )
    cr = result.scalar_one_or_none()
    if cr is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Condensed role not found")

    if body.name is not None:
        cr.name = body.name

    if body.role_ids is not None:
        # Replace all mappings
        for m in list(cr.mappings):
            await db.delete(m)
        await db.flush()
        for role_id in body.role_ids:
            db.add(CondensedRoleMapping(condensed_role_id=cr.id, role_id=role_id))

    await db.commit()
    await db.refresh(cr)
    return await _build_response(cr, db)


@router.delete("/{condensed_role_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_condensed_role(
    condensed_role_id: str,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> None:
    result = await db.execute(
        select(CondensedRole).where(
            CondensedRole.id == condensed_role_id,
            CondensedRole.company_id == current_user.company_id,
        )
    )
    cr = result.scalar_one_or_none()
    if cr is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Condensed role not found")

    await db.delete(cr)
    await db.commit()
