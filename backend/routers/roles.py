import csv
import io
import json

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.dependencies import get_db, require_manager
from backend.models import Role, User
from backend.schemas.role import RoleBulkUploadResponse, RoleCreate, RoleResponse, RoleUpdate

router = APIRouter(prefix="/roles", tags=["roles"])


@router.get("/", response_model=list[RoleResponse])
async def list_roles(
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> list[RoleResponse]:
    result = await db.execute(
        select(Role).where(Role.company_id == current_user.company_id).order_by(Role.name)
    )
    roles = result.scalars().all()
    return [RoleResponse.model_validate(r) for r in roles]


@router.post("/", response_model=RoleResponse, status_code=status.HTTP_201_CREATED)
async def create_role(
    body: RoleCreate,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> RoleResponse:
    role = Role(
        company_id=current_user.company_id,
        name=body.name,
        description=body.description,
    )
    db.add(role)
    await db.commit()
    await db.refresh(role)
    return RoleResponse.model_validate(role)


@router.put("/{role_id}", response_model=RoleResponse)
async def update_role(
    role_id: str,
    body: RoleUpdate,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> RoleResponse:
    result = await db.execute(
        select(Role).where(Role.id == role_id, Role.company_id == current_user.company_id)
    )
    role = result.scalar_one_or_none()
    if role is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Role not found")

    if body.name is not None:
        role.name = body.name
    if body.description is not None:
        role.description = body.description

    await db.commit()
    await db.refresh(role)
    return RoleResponse.model_validate(role)


@router.post("/bulk-upload", response_model=RoleBulkUploadResponse)
async def bulk_upload_roles(
    file: UploadFile,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> RoleBulkUploadResponse:
    """
    Accepts a CSV or JSON file for bulk role import.

    CSV columns: name, description
    JSON format: [{"name": "...", "description": "..."}]

    Duplicate role names (case-insensitive) within the company are skipped.
    """
    # Body size + row-count caps (#46, matching employees.py::bulk_upload).
    # Free registration removed the paywall that used to sit in front of
    # this endpoint, so an unauthenticated-cost-free upload must not be
    # allowed to read an unbounded body or insert unbounded rows.
    _MAX_BODY_BYTES = 5 * 1024 * 1024  # 5 MB
    _MAX_ROWS = 10_000

    content = await file.read()
    if len(content) > _MAX_BODY_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=(
                f"Upload exceeds {_MAX_BODY_BYTES // (1024 * 1024)} MB limit "
                f"({len(content)} bytes)."
            ),
        )
    text = content.decode("utf-8-sig")

    is_json = (
        (file.content_type and "json" in file.content_type)
        or (file.filename and file.filename.endswith(".json"))
    )

    if is_json:
        try:
            rows = json.loads(text)
        except json.JSONDecodeError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid JSON: {e}",
            )
        if not isinstance(rows, list):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="JSON must be an array of objects",
            )
    else:
        reader = csv.DictReader(io.StringIO(text))
        rows = list(reader)

    if len(rows) > _MAX_ROWS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Upload contains {len(rows)} rows; the per-request maximum "
                f"is {_MAX_ROWS}. Split the file and retry."
            ),
        )

    # Pre-fetch existing role names for dedup
    role_result = await db.execute(
        select(Role.name).where(Role.company_id == current_user.company_id)
    )
    existing_names: set[str] = {name.lower() for name in role_result.scalars().all()}

    created = 0
    skipped = 0
    errors: list[str] = []

    for row_num, row in enumerate(rows, start=2 if not is_json else 1):
        name = (row.get("name") or "").strip()
        description = (row.get("description") or "").strip() or None

        if not name:
            errors.append(f"Row {row_num}: missing name, skipped")
            skipped += 1
            continue

        if name.lower() in existing_names:
            errors.append(f"Row {row_num}: role '{name}' already exists, skipped")
            skipped += 1
            continue

        role = Role(
            company_id=current_user.company_id,
            name=name,
            description=description,
        )
        db.add(role)
        existing_names.add(name.lower())
        created += 1

    await db.commit()
    return RoleBulkUploadResponse(created=created, skipped=skipped, errors=errors)


@router.delete("/{role_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_role(
    role_id: str,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> None:
    result = await db.execute(
        select(Role).where(Role.id == role_id, Role.company_id == current_user.company_id)
    )
    role = result.scalar_one_or_none()
    if role is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Role not found")

    await db.delete(role)
    await db.commit()
