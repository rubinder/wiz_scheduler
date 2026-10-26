import csv
import io
import json

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.dependencies import get_db, get_location_scope, require_admin, require_manager
from backend.models import Location, Region, User
from backend.schemas.location import LocationBulkUploadResponse, LocationCreate, LocationResponse, LocationUpdate
from backend.services.billing import utc_month_start
from backend.services.archive import active, archive_location, restore_location
from backend.services.location_scope import LocationScope
from backend.services.ownership import assert_owned
from backend.services.plan import assert_can_add, assert_paid_plan
from backend.utils.tz import is_valid_timezone

router = APIRouter(prefix="/locations", tags=["locations"])


@router.get("/", response_model=list[LocationResponse])
async def list_locations(
    include_archived: bool = False,
    current_user: User = Depends(require_manager),
    scope: LocationScope = Depends(get_location_scope),
    db: AsyncSession = Depends(get_db),
) -> list[LocationResponse]:
    # A regular manager sees only their assigned locations (#149).
    query = select(Location).where(
        Location.company_id == current_user.company_id, scope.clause(Location.id),
    )
    if not include_archived:
        query = query.where(active(Location))
    result = await db.execute(query)
    locations = result.scalars().all()
    return [LocationResponse.model_validate(loc) for loc in locations]


@router.post("/", response_model=LocationResponse, status_code=status.HTTP_201_CREATED)
async def create_location(
    body: LocationCreate,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> LocationResponse:
    await assert_can_add(db, str(current_user.company_id), locations=1)
    if body.overtime_threshold_hours is not None or body.overtime_premium_multiplier is not None:
        await assert_paid_plan(db, str(current_user.company_id), "cost_aware_scheduling")

    await assert_owned(db, Region, [body.region_id], [current_user.company_id], "Region")
    location = Location(
        company_id=current_user.company_id,
        region_id=body.region_id,
        name=body.name,
        address=body.address,
        geo_coord=body.geo_coord,
        timezone=body.timezone,
        min_rest_hours=body.min_rest_hours,
        overtime_threshold_hours=body.overtime_threshold_hours,
        overtime_premium_multiplier=body.overtime_premium_multiplier,
    )
    db.add(location)
    await db.commit()
    await db.refresh(location)

    # Activation funnel (#115), fired after the location durably commits.
    # record_milestone runs on its own private session, so a milestone
    # failure can never fail the create or disturb `location`.
    from backend.services.activation import record_milestone
    from backend.services.billing import get_ownership_group_id

    og_id = await get_ownership_group_id(db, str(current_user.company_id))
    await record_milestone(
        db, og_id, "first_location", user_id=str(current_user.id)
    )

    return LocationResponse.model_validate(location)


@router.put("/{location_id}", response_model=LocationResponse)
async def update_location(
    location_id: str,
    body: LocationUpdate,
    current_user: User = Depends(require_manager),
    scope: LocationScope = Depends(get_location_scope),
    db: AsyncSession = Depends(get_db),
) -> LocationResponse:
    # Out of scope answers 404, like another company's location (#149).
    scope.require(location_id, "Location")
    result = await db.execute(
        select(Location).where(
            Location.id == location_id,
            Location.company_id == current_user.company_id,
        )
    )
    location = result.scalar_one_or_none()
    if location is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Location not found")

    # Gate on the VALUE being set, not just the field's presence, so a
    # free-plan location can clear stale overtime settings (set to null)
    # without needing to re-upgrade -- clearing never grants paid-tier behavior.
    if body.overtime_threshold_hours is not None or body.overtime_premium_multiplier is not None:
        await assert_paid_plan(db, str(current_user.company_id), "cost_aware_scheduling")

    if body.region_id is not None:
        await assert_owned(db, Region, [body.region_id], [current_user.company_id], "Region")
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
    if "overtime_threshold_hours" in body.model_fields_set:
        location.overtime_threshold_hours = body.overtime_threshold_hours
    if "overtime_premium_multiplier" in body.model_fields_set:
        location.overtime_premium_multiplier = body.overtime_premium_multiplier

    # Update signal_config if provided (gated on paid plan for signal weight features)
    if "signal_config" in body.model_fields_set:
        if body.signal_config is not None:
            await assert_paid_plan(db, str(current_user.company_id), "signal_weight_scheduling")
        location.signal_config = body.signal_config

    await db.commit()
    await db.refresh(location)
    return LocationResponse.model_validate(location)


@router.post("/bulk-upload", response_model=LocationBulkUploadResponse)
async def bulk_upload_locations(
    file: UploadFile,
    default_timezone: str | None = Form(None),
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> LocationBulkUploadResponse:
    """
    Accepts a CSV or JSON file for bulk location import.

    CSV columns: name, region_name, address, timezone
    JSON format: [{"name": "...", "region_name": "...", "address": "...", "timezone": "..."}]

    Region names are matched case-insensitively against the regions table.
    Blank timezone cells use the multipart field default_timezone
    (the caller's browser zone), else UTC.
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

    if len(rows) > _MAX_ROWS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Upload contains {len(rows)} rows; the per-request maximum "
                f"is {_MAX_ROWS}. Split the file and retry."
            ),
        )

    fallback_tz = (default_timezone or "").strip()
    if not is_valid_timezone(fallback_tz):
        fallback_tz = "UTC"

    # Pre-fetch regions for case-insensitive matching
    region_result = await db.execute(
        select(Region).where(Region.company_id == current_user.company_id)
    )
    regions_by_name: dict[str, Region] = {
        r.name.lower(): r for r in region_result.scalars().all()
    }

    # Pre-fetch existing location names to detect duplicates
    loc_result = await db.execute(
        select(Location.name, Location.archived_at).where(Location.company_id == current_user.company_id)
    )
    existing_names: set[str] = set()
    archived_names: set[str] = set()
    for loc_name, loc_archived_at in loc_result.all():
        existing_names.add(loc_name.lower())
        if loc_archived_at is not None:
            archived_names.add(loc_name.lower())

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
        row_tz = (row.get("timezone") or "").strip() or fallback_tz

        if not name:
            errors.append(f"Row {row_num}: missing name, skipped")
            skipped += 1
            continue

        if name.lower() in existing_names:
            if name.lower() in archived_names:
                errors.append(f"Row {row_num}: {name}: exists (archived) — restore it instead")
            else:
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

        if not is_valid_timezone(row_tz):
            errors.append(
                f"Row {row_num}: timezone '{row_tz}' is not a valid IANA timezone, skipped"
            )
            skipped += 1
            continue

        location = Location(
            company_id=current_user.company_id,
            region_id=region.id,
            name=name,
            address=address,
            timezone=row_tz,
        )
        db.add(location)
        existing_names.add(name.lower())
        created += 1

    await db.commit()

    # Activation funnel (#115): a bulk import creates locations exactly as
    # much as the single-create endpoint does, so it must count too.
    if created > 0:
        from backend.services.activation import record_milestone
        from backend.services.billing import get_ownership_group_id

        og_id = await get_ownership_group_id(db, str(current_user.company_id))
        await record_milestone(
            db, og_id, "first_location", user_id=str(current_user.id)
        )

    return LocationBulkUploadResponse(created=created, skipped=skipped, errors=errors)


@router.delete("/{location_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_location(
    location_id: str,
    current_user: User = Depends(require_admin),
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

    await archive_location(db, current_user.company_id, location)
    await db.commit()


@router.post("/{location_id}/restore", response_model=LocationResponse)
async def restore_location_endpoint(
    location_id: str,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> LocationResponse:
    location = (await db.execute(select(Location).where(
        Location.id == location_id, Location.company_id == current_user.company_id,
    ))).scalar_one_or_none()
    if location is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Location not found")
    if location.archived_at is not None:
        # A location archived this month still holds its plan slot, so
        # restoring it adds nothing; only an older one needs a free slot.
        if location.archived_at < utc_month_start():
            await assert_can_add(db, str(current_user.company_id), locations=1)
        await restore_location(db, current_user.company_id, location)
        await db.commit()
        await db.refresh(location)
    return LocationResponse.model_validate(location)
