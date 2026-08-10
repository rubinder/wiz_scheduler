import csv
import io
import json

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.dependencies import get_db, require_manager
from backend.models import Location, Region, User
from backend.schemas.location import LocationBulkUploadResponse, LocationCreate, LocationResponse, LocationUpdate
from backend.services.plan import assert_can_add

router = APIRouter(prefix="/locations", tags=["locations"])


@router.get("/", response_model=list[LocationResponse])
async def list_locations(
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> list[LocationResponse]:
    result = await db.execute(
        select(Location).where(Location.company_id == current_user.company_id)
    )
    locations = result.scalars().all()
    return [LocationResponse.model_validate(loc) for loc in locations]


@router.post("/", response_model=LocationResponse, status_code=status.HTTP_201_CREATED)
async def create_location(
    body: LocationCreate,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> LocationResponse:
    await assert_can_add(db, str(current_user.company_id), locations=1)

    location = Location(
        company_id=current_user.company_id,
        region_id=body.region_id,
        name=body.name,
        address=body.address,
        geo_coord=body.geo_coord,
        timezone=body.timezone,
        min_rest_hours=body.min_rest_hours,
    )
    db.add(location)
    await db.commit()
    await db.refresh(location)
    return LocationResponse.model_validate(location)


@router.put("/{location_id}", response_model=LocationResponse)
async def update_location(
    location_id: str,
    body: LocationUpdate,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> LocationResponse:
    result = await db.execute(
        select(Location).where(
            Location.id == location_id,
            Location.company_id == current_user.company_id,
        )
    )
    location = result.scalar_one_or_none()
    if location is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Location not found")

    if body.region_id is not None:
        location.region_id = body.region_id
    if body.name is not None:
        location.name = body.name
    if body.address is not None:
        location.address = body.address
    if body.geo_coord is not None:
        location.geo_coord = body.geo_coord
    if body.timezone is not None:
        location.timezone = body.timezone
    # Nullable + clearable: send {"min_rest_hours": null} to remove the rule.
    if "min_rest_hours" in body.model_fields_set:
        location.min_rest_hours = body.min_rest_hours

    await db.commit()
    await db.refresh(location)
    return LocationResponse.model_validate(location)


@router.post("/bulk-upload", response_model=LocationBulkUploadResponse)
async def bulk_upload_locations(
    file: UploadFile,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> LocationBulkUploadResponse:
    """
    Accepts a CSV or JSON file for bulk location import.

    CSV columns: name, region_name, address, timezone
    JSON format: [{"name": "...", "region_name": "...", "address": "...", "timezone": "..."}]

    Region names are matched case-insensitively against the regions table.
    """
    content = await file.read()
    text = content.decode("utf-8-sig")

    # Determine format from content type or filename
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

    # Pre-fetch regions for case-insensitive matching
    region_result = await db.execute(
        select(Region).where(Region.company_id == current_user.company_id)
    )
    regions_by_name: dict[str, Region] = {
        r.name.lower(): r for r in region_result.scalars().all()
    }

    # Pre-fetch existing location names to detect duplicates
    loc_result = await db.execute(
        select(Location.name).where(Location.company_id == current_user.company_id)
    )
    existing_names: set[str] = {name.lower() for name in loc_result.scalars().all()}

    # Free-plan cap. Checked after parsing (we need the row count) but before
    # any insert, so an oversized file is refused whole rather than filling
    # to the limit — a silently truncated roster reads as a complete one.
    if rows:
        await assert_can_add(db, str(current_user.company_id), locations=len(rows))

    created = 0
    skipped = 0
    errors: list[str] = []

    for row_num, row in enumerate(rows, start=2 if not is_json else 1):
        name = (row.get("name") or "").strip()
        region_name = (row.get("region_name") or "").strip()
        address = (row.get("address") or "").strip() or None
        timezone = (row.get("timezone") or "").strip() or "UTC"

        if not name:
            errors.append(f"Row {row_num}: missing name, skipped")
            skipped += 1
            continue

        if name.lower() in existing_names:
            errors.append(f"Row {row_num}: location '{name}' already exists, skipped")
            skipped += 1
            continue

        if not region_name:
            errors.append(f"Row {row_num}: missing region_name, skipped")
            skipped += 1
            continue

        region = regions_by_name.get(region_name.lower())
        if region is None:
            errors.append(f"Row {row_num}: region '{region_name}' not found, skipped")
            skipped += 1
            continue

        location = Location(
            company_id=current_user.company_id,
            region_id=region.id,
            name=name,
            address=address,
            timezone=timezone,
        )
        db.add(location)
        existing_names.add(name.lower())
        created += 1

    await db.commit()
    return LocationBulkUploadResponse(created=created, skipped=skipped, errors=errors)


@router.delete("/{location_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_location(
    location_id: str,
    current_user: User = Depends(require_manager),
    db: AsyncSession = Depends(get_db),
) -> None:
    result = await db.execute(
        select(Location).where(
            Location.id == location_id,
            Location.company_id == current_user.company_id,
        )
    )
    location = result.scalar_one_or_none()
    if location is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Location not found")

    await db.delete(location)
    await db.commit()
