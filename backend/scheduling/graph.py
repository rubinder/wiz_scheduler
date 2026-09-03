import logging
from datetime import date as date_type, datetime, timedelta, timezone
from typing import Any, AsyncGenerator, Dict, List, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from langgraph.graph import END, StateGraph
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import (
    Company,
    CondensedRoleMapping,
    Employee,
    EmployeeAffinity,
    EmployeeAvailability,
    EmployeeDayBlackout,
    EmployeeDayPreference,
    EmployeeHourRangeCap,
    EmployeeHourRangePreference,
    EmployeeRole,
    Location,
    Role,
    Shift,
    ShiftSchedule,
    ShiftTemplate,
)
from backend.scheduling.local_scheduler import Strategy, local_schedule
from backend.scheduling.nodes import (
    _subtract_consumed,
    build_prompt,
    call_llm,
    emit_result,
    load_location_context,
    parse_schedule,
    validate_schedule,
    validate_and_update_availability,
)
from backend.scheduling.state import FailureEntry, LocationResult, SchedulingState
from backend.scheduling.template_resolver import (
    LocationMissingTemplate,
    resolve_templates_for_week,
)

logger = logging.getLogger(__name__)


_DAY_NAMES = [
    "Monday", "Tuesday", "Wednesday", "Thursday",
    "Friday", "Saturday", "Sunday",
]


def _hhmm(value: Any) -> str:
    """Coerce a stored time string ('HH:MM' or 'HH:MM:SS') to 'HH:MM'."""
    if not isinstance(value, str) or len(value) < 5:
        return str(value or "")
    return value[:5]


def _normalize_template_slots_for_dow(
    weekly_schedule_raw: Any,
    target_dow: int,
    role_map: Dict[str, str],
) -> List[Dict[str, Any]]:
    """Extract the slot list for *target_dow* from a ShiftTemplate.weekly_schedule.

    Handles three storage shapes seen in the codebase:
      1. Legacy flat list of per-day slots:
         [{"day": "Monday", "role_name": "...", "role_id": "...",
           "headcount": N, "start_time": "HH:MM", "end_time": "HH:MM"}, ...]
      2. Legacy dict keyed by day name:
         {"Monday": [{...slot...}, ...], ...}
      3. New per-dow shape (used by clone_template_for_date for specific-date
         overrides and by future UI saves):
         [{"day_of_week": int, "roles": [{"role_name": "...", "role_id": "...",
           "headcount": N, "start_time": "HH:MM:SS", "end_time": "HH:MM:SS"}]}]

    Returns the canonical list-of-slots in the legacy slot shape that the rest
    of the scheduling pipeline already understands.
    """
    day_name = _DAY_NAMES[target_dow % 7]
    raw_slots: List[Dict[str, Any]] = []

    if isinstance(weekly_schedule_raw, list):
        # Distinguish shape (3) from shape (1): shape (3) entries have
        # "day_of_week" + "roles"; shape (1) entries have "day" + flat fields.
        if weekly_schedule_raw and isinstance(weekly_schedule_raw[0], dict) and (
            "day_of_week" in weekly_schedule_raw[0]
            or "roles" in weekly_schedule_raw[0]
        ):
            for entry in weekly_schedule_raw:
                if entry.get("day_of_week") != target_dow:
                    continue
                for role in entry.get("roles", []) or []:
                    raw_slots.append({
                        "role_name": role.get("role_name", ""),
                        "role_id": role.get("role_id", ""),
                        "headcount": role.get("headcount", 1),
                        "start_time": _hhmm(role.get("start_time", "")),
                        "end_time": _hhmm(role.get("end_time", "")),
                    })
        else:
            for slot in weekly_schedule_raw:
                if slot.get("day") != day_name:
                    continue
                raw_slots.append(dict(slot))
    elif isinstance(weekly_schedule_raw, dict):
        for slot in weekly_schedule_raw.get(day_name, []) or []:
            raw_slots.append(dict(slot))

    # Enrich each slot with role_name from the role_map if missing.
    enriched: List[Dict[str, Any]] = []
    for slot in raw_slots:
        rid = slot.get("role_id", "")
        if rid and rid in role_map and not slot.get("role_name"):
            slot["role_name"] = role_map[rid]
        # Normalise time fields (some shapes use HH:MM:SS).
        if "start_time" in slot:
            slot["start_time"] = _hhmm(slot["start_time"])
        if "end_time" in slot:
            slot["end_time"] = _hhmm(slot["end_time"])
        enriched.append(slot)
    return enriched


def _should_retry_or_emit(state: SchedulingState) -> str:
    """After validate_and_update_availability, decide next step.

    If there are conflicts and retry_count < max (1 retry allowed), go back to
    build_prompt (which will include conflict notes). Otherwise proceed to emit.
    """
    has_conflict = any(s["status"] == "CONFLICT" for s in state.get("current_parsed_shifts", []))
    conflict_notes = state.get("conflict_notes", "")

    # If retry_count was just incremented (meaning we haven't retried yet),
    # the conflict_notes will be non-empty and parsed_shifts won't have CONFLICT status yet
    if conflict_notes and not has_conflict and state.get("retry_count", 0) == 1:
        return "build_prompt"

    return "emit_result"


def _should_continue_or_end(state: SchedulingState) -> str:
    """After emit_result, decide whether to process the next location or finish."""
    if state["current_location_index"] < len(state["locations"]):
        return "load_location_context"
    return END


def build_scheduling_graph(
    use_local: bool = False,
    strategy: Strategy = "random",
    strategy_param: float = 0.5,
    strategy_param2: float = 0.0,
) -> StateGraph:
    """Construct the LangGraph state graph for the scheduling pipeline.

    Args:
        use_local: If True, use the local algorithmic scheduler instead of
            the LLM.  Skips prompt building and LLM call entirely.
        strategy: Scheduling strategy for the local scheduler ("random",
            "rotation", or "rotation_history").  Ignored when *use_local* is
            False.
        strategy_param: Fairness weight for rotation_history (0.0-1.0).
            Ignored for other strategies.
    """
    graph = StateGraph(SchedulingState)

    # Add nodes
    graph.add_node("load_location_context", load_location_context)
    graph.add_node("validate_schedule", validate_schedule)
    graph.add_node("validate_and_update_availability", validate_and_update_availability)
    graph.add_node("emit_result", emit_result)

    # Set entry point
    graph.set_entry_point("load_location_context")

    if use_local:
        # Local scheduler: load context -> local_schedule -> validate
        def _local_schedule_node(state: SchedulingState) -> Dict[str, Any]:
            return local_schedule(state, strategy=strategy, strategy_param=strategy_param, strategy_param2=strategy_param2)

        graph.add_node("local_schedule", _local_schedule_node)
        graph.add_edge("load_location_context", "local_schedule")
        graph.add_edge("local_schedule", "validate_schedule")
    else:
        # LLM path: load context -> prompt -> llm -> parse -> validate
        graph.add_node("build_prompt", build_prompt)
        graph.add_node("call_llm", call_llm)
        graph.add_node("parse_schedule", parse_schedule)
        graph.add_edge("load_location_context", "build_prompt")
        graph.add_edge("build_prompt", "call_llm")
        graph.add_edge("call_llm", "parse_schedule")
        graph.add_edge("parse_schedule", "validate_schedule")

    graph.add_edge("validate_schedule", "validate_and_update_availability")

    # Conditional edge after validation: retry or emit
    if use_local:
        # Local scheduler doesn't retry — go straight to emit
        graph.add_edge("validate_and_update_availability", "emit_result")
    else:
        graph.add_conditional_edges(
            "validate_and_update_availability",
            _should_retry_or_emit,
            {
                "build_prompt": "build_prompt",
                "emit_result": "emit_result",
            },
        )

    # Conditional edge after emit: next location or END
    graph.add_conditional_edges(
        "emit_result",
        _should_continue_or_end,
        {
            "load_location_context": "load_location_context",
            END: END,
        },
    )

    return graph


# A generation covers at most one calendar week.
#
# Enforced in two places on purpose. GenerateRequest.num_days carries
# Field(ge=1, le=7) so the API rejects a wider window with a 422, and this
# guard catches any internal caller that bypasses the schema. Without it a
# wider window silently corrupts the result rather than failing: the per-day
# template fusion below keys the fused weekly_schedule by day NAME, so an
# 8-day window contains two Mondays and the later date's override quietly
# overwrites the earlier one.
#
# It also bounds the free plan. FREE_PLAN_SCHEDULES_PER_LOCATION counts
# schedules, not days, so an unbounded window would let one free schedule per
# location cover a year.
MAX_SCHEDULE_DAYS = 7


def _validate_num_days(num_days: int) -> None:
    """Raise ValueError if *num_days* is outside 1..MAX_SCHEDULE_DAYS."""
    if not 1 <= num_days <= MAX_SCHEDULE_DAYS:
        raise ValueError(
            f"num_days must be between 1 and {MAX_SCHEDULE_DAYS} "
            f"(one calendar week); got {num_days}"
        )


def _shift_local_face(
    shift: Shift,
    location: Location,
    *,
    keep_tzinfo: bool = False,
) -> Tuple[datetime, datetime] | None:
    """Recover the wall-clock face a committed Shift occupies at its location.

    Shift.start_time/end_time are timestamptz columns -- true instants, NOT
    wall-clock-tagged-UTC like availability. local_scheduler.py emits an
    aware datetime with the location's real offset (e.g. "09:00:00-04:00");
    approve parses that and Postgres normalises it to UTC on storage, so it
    comes back as "13:00:00+00:00". Stripping the tag at that point (the way
    _wall_clock does for availability, in nodes.py, #85) reads the WRONG
    face -- 13:00, not the 09:00 the shift actually covers.

    The fix is to convert the instant into ITS OWN location's zone and take
    THAT face. This does not violate the "never .astimezone()" rule from
    #61/#85: that rule protects availability, which is a wall-clock value
    falsely tagged UTC and therefore NOT a true instant -- calling
    .astimezone() on it would move the face it represents. A Shift timestamp
    IS a true instant, so .astimezone(location's zone) recovers the intended
    face instead of moving it. The two columns are both "aware datetime"
    attributes and look interchangeable; they are not, and that similarity is
    exactly what made the original bug (reading the UTC-normalised face
    directly) easy to write and easy to miss.

    One more wrinkle: this only matters for a driver that actually preserves
    the offset. SQLite's DateTime(timezone=True) columns drop tzinfo on
    read -- the value that comes back is naive but its digits are still the
    original local face (SQLite never normalises to UTC the way Postgres
    does). Calling .astimezone() on THAT naive value would misinterpret it as
    the host's system timezone and move it again, introducing a second bug.
    So the conversion below only applies when the value is actually aware; a
    naive value is trusted as already being the correct face.

    By default the returned datetimes are naive (tzinfo stripped) -- the shape
    every internal comparison against other wall-clock values (availability,
    other shifts) expects. Pass keep_tzinfo=True to get an aware datetime in
    the location's own zone instead of a naive one; the only caller that wants
    that is one serializing the face back out to a client (see
    `_shift_to_response`, backend/routers/schedules.py), which needs the
    offset itself to survive the round trip.

    Returns None -- rather than raising -- if location.timezone is missing,
    invalid, or the shift's timestamps are unusable: the scheduling graph
    degrades, it never throws.
    """
    tz_name = getattr(location, "timezone", None)
    if not tz_name:
        logger.warning(
            "[SCHED-TRACE] shift %s has no usable location.timezone (%r); "
            "dropping its hold and releasing the hours it covers",
            getattr(shift, "id", "?"), tz_name,
        )
        return None
    try:
        tz = ZoneInfo(tz_name)
        start_raw = shift.start_time
        end_raw = shift.end_time
        if keep_tzinfo:
            start = start_raw.astimezone(tz) if start_raw.tzinfo is not None else start_raw
            end = end_raw.astimezone(tz) if end_raw.tzinfo is not None else end_raw
        else:
            start = (
                start_raw.astimezone(tz).replace(tzinfo=None)
                if start_raw.tzinfo is not None else start_raw
            )
            end = (
                end_raw.astimezone(tz).replace(tzinfo=None)
                if end_raw.tzinfo is not None else end_raw
            )
    except (AttributeError, ValueError, TypeError, ZoneInfoNotFoundError):
        logger.warning(
            "[SCHED-TRACE] shift %s has an unparseable timezone (%r); "
            "dropping its hold and releasing the hours it covers",
            getattr(shift, "id", "?"), tz_name,
        )
        return None
    return start, end


async def _committed_shifts_by_employee(
    db: AsyncSession,
    company_id: str,
    week_start: datetime,
    week_end: datetime,
) -> Dict[str, List[Tuple[datetime, datetime]]]:
    """Already-committed shift spans for the week, per employee.

    Returned as naive wall-clock pairs, comparable face-to-face with
    availability (see _shift_local_face for why Shift timestamps need their
    own conversion rather than a tag-strip).

    The SQL window is widened by a day on each side because Shift.start_time
    is a UTC instant: comparing it directly against the wall-clock week
    boundary can silently drop (or wrongly include) a shift whose instant has
    rolled across the UTC day line relative to its location's offset. The
    face-based filter below narrows back down to the true week using the
    same converted face used for subtraction.
    """
    rows = (await db.execute(
        select(Shift, Location)
        .join(Location, Shift.location_id == Location.id)
        .where(
            Shift.company_id == company_id,
            Location.company_id == company_id,
            Shift.start_time >= week_start - timedelta(days=1),
            Shift.start_time < week_end + timedelta(days=1),
        )
    )).all()

    naive_week_start = week_start.replace(tzinfo=None)
    naive_week_end = week_end.replace(tzinfo=None)

    by_emp: Dict[str, List[Tuple[datetime, datetime]]] = {}
    for s, loc in rows:
        face = _shift_local_face(s, loc)
        if face is None:
            continue
        start, end = face
        if start < naive_week_start or start >= naive_week_end:
            continue
        by_emp.setdefault(str(s.employee_id), []).append((start, end))
    return by_emp


def _subtract_committed_shifts(
    emp_avail_map: Dict[str, List[Dict[str, str]]],
    committed: Dict[str, List[Tuple[datetime, datetime]]],
) -> Dict[str, List[Dict[str, str]]]:
    """Carve each employee's committed shifts out of their availability.

    Output timestamps are rebuilt with each window's own original offset, so
    the shape handed to the pipeline is unchanged.
    """
    out: Dict[str, List[Dict[str, str]]] = {}
    for eid, windows in emp_avail_map.items():
        spans = committed.get(eid, [])
        if not spans:
            out[eid] = windows
            continue

        rebuilt: List[Dict[str, str]] = []
        for w in windows:
            try:
                start_aware = datetime.fromisoformat(w["start"])
                end_aware = datetime.fromisoformat(w["end"])
            except (KeyError, ValueError, TypeError):
                rebuilt.append(w)
                continue

            tz = start_aware.tzinfo
            for p_start, p_end in _subtract_consumed(
                start_aware.replace(tzinfo=None), end_aware.replace(tzinfo=None), spans,
            ):
                rebuilt.append({
                    "start": p_start.replace(tzinfo=tz).isoformat(),
                    "end": p_end.replace(tzinfo=tz).isoformat(),
                })
        out[eid] = rebuilt
    return out


async def _load_employee_availability(
    db: AsyncSession,
    company_id: str,
    week_start_date: str,
    num_days: int,
    all_employee_ids: set[str] | None = None,
) -> Dict[str, List[Dict[str, str]]]:
    """Employee availability for the week, minus hours already committed.

    A Shift row IS a hold: rather than carving employee_availability at
    approve time (which destroyed it irreversibly), consumption is derived
    here at read time. Deleting a shift therefore releases its hold with no
    separate release step, and the two cannot disagree.

    Both scheduling paths load availability through this one function, so
    neither can bypass it.
    """
    week_start = datetime.strptime(week_start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    week_end = week_start + timedelta(days=num_days)

    avail_result = await db.execute(
        select(EmployeeAvailability).where(
            EmployeeAvailability.company_id == company_id,
            EmployeeAvailability.start_time >= week_start,
            EmployeeAvailability.start_time < week_end,
        )
    )
    emp_avail_map: Dict[str, List[Dict[str, str]]] = {}
    for av in avail_result.scalars().all():
        eid = str(av.employee_id)
        emp_avail_map.setdefault(eid, [])

        start_dt = av.start_time
        end_dt = av.end_time
        # Availability is contractually local wall-clock TAGGED UTC (#61); if
        # the driver round-tripped it as naive (SQLite drops tzinfo on
        # DateTime(timezone=True) columns; Postgres does not), restore that
        # tag rather than leaving it ambiguous. This re-attaches the tag the
        # value has always meant -- it is not an instant conversion.
        if start_dt is not None and start_dt.tzinfo is None:
            start_dt = start_dt.replace(tzinfo=timezone.utc)
        if end_dt is not None and end_dt.tzinfo is None:
            end_dt = end_dt.replace(tzinfo=timezone.utc)
        # Fix midnight end times: if end <= start, the end_time was stored as
        # 00:00:00 on the same date (meaning "end of day"), so bump it to 23:59.
        if start_dt and end_dt and end_dt <= start_dt:
            end_dt = start_dt.replace(hour=23, minute=59, second=0, microsecond=0)

        emp_avail_map[eid].append({
            "start": start_dt.isoformat() if hasattr(start_dt, "isoformat") else str(start_dt),
            "end": end_dt.isoformat() if hasattr(end_dt, "isoformat") else str(end_dt),
        })

    emp_avail_map = _subtract_committed_shifts(
        emp_avail_map,
        await _committed_shifts_by_employee(db, company_id, week_start, week_end),
    )

    # Employees with NO availability records are treated as "not available".
    # They get an empty list so the LLM and validator will not schedule them
    # until explicit availability is provided.
    for eid in (all_employee_ids or set()) - set(emp_avail_map.keys()):
        emp_avail_map[eid] = []

    return emp_avail_map


async def _load_initial_state(
    company_id: str,
    week_start_date: str,
    db: AsyncSession,
    template_ids: List[str] | None = None,
    num_days: int = 7,
) -> Dict[str, Any]:
    """Query the database to build the initial SchedulingState."""
    _validate_num_days(num_days)

    # Load roles for name lookup
    role_result = await db.execute(
        select(Role).where(Role.company_id == company_id)
    )
    roles_orm = role_result.scalars().all()
    role_map: Dict[str, str] = {str(r.id): r.name for r in roles_orm}

    # Load condensed roles for name lookup and mappings
    from backend.models.condensed_role import CondensedRole as CondensedRoleModel
    cr_result = await db.execute(
        select(CondensedRoleModel).where(CondensedRoleModel.company_id == company_id)
    )
    condensed_roles_orm = cr_result.scalars().all()
    condensed_role_name_map: Dict[str, str] = {
        str(cr.id): cr.name for cr in condensed_roles_orm
    }

    # Load condensed role mappings — build role_id -> set of equivalent role_ids
    crm_result = await db.execute(
        select(CondensedRoleMapping).join(
            CondensedRoleMapping.condensed_role
        ).where(
            CondensedRoleMapping.condensed_role.has(company_id=company_id)
        )
    )
    crm_rows = crm_result.scalars().all()
    # Group role_ids by condensed_role_id
    condensed_groups: Dict[str, List[str]] = {}
    for crm in crm_rows:
        cid = str(crm.condensed_role_id)
        condensed_groups.setdefault(cid, []).append(str(crm.role_id))
    # Build role_id -> set of all equivalent role_ids (including itself)
    role_equivalents: Dict[str, set[str]] = {}
    for group in condensed_groups.values():
        group_set = set(group)
        for rid in group:
            role_equivalents.setdefault(rid, set()).update(group_set)

    # Build member_role_id -> list of condensed_role_ids the role belongs to
    role_to_condensed: Dict[str, List[str]] = {}
    for cid, member_ids in condensed_groups.items():
        for rid in member_ids:
            role_to_condensed.setdefault(rid, []).append(cid)

    # Merge condensed role names into role_map so shift template slots
    # that reference condensed role IDs resolve to a name
    role_map.update(condensed_role_name_map)

    # Determine which locations need scheduling.
    #
    # The legacy behaviour drove this from ShiftTemplate.id matches against
    # template_ids. Per-day templates change that: a manager selects
    # *recurring* templates (one per location to schedule) and the resolver
    # picks specific_date overrides automatically. So we resolve "locations to
    # schedule" from the recurring templates that match template_ids (or all
    # recurring templates if none provided), then call resolve_templates_for_
    # week per location to fuse in any overrides.
    recurring_q = select(ShiftTemplate).where(
        ShiftTemplate.company_id == company_id,
        ShiftTemplate.specific_date.is_(None),
    )
    if template_ids:
        recurring_q = recurring_q.where(ShiftTemplate.id.in_(template_ids))
    recurring_rows = (await db.execute(recurring_q)).scalars().all()

    # Build the list of dates in the schedule window once; we'll use it per
    # location for resolver lookups and per-day fusion.
    week_start_d = date_type.fromisoformat(week_start_date)
    week_dates: List[date_type] = [
        week_start_d + timedelta(days=i) for i in range(num_days)
    ]

    # Group recurring template ids per location. Each location should normally
    # have at most one selected recurring template, but the resolver picks the
    # first deterministically if there are several.
    location_to_template_ids: Dict[str, List[str]] = {}
    for tmpl in recurring_rows:
        loc_id = str(tmpl.location_id)
        location_to_template_ids.setdefault(loc_id, []).append(str(tmpl.id))

    selected_location_ids: set[str] = set(location_to_template_ids.keys())

    pipeline_errors: List[str] = []
    pipeline_failure_entries: List[Dict[str, Any]] = []
    shift_templates: Dict[str, Dict[str, Any]] = {}

    for loc_id, tmpl_ids in location_to_template_ids.items():
        try:
            per_date_templates = await resolve_templates_for_week(
                db,
                location_id=loc_id,
                week_dates=week_dates,
                selected_template_ids=tmpl_ids,
            )
        except LocationMissingTemplate as exc:
            error_msg = (
                f"TEMPLATE_RESOLVE_ERROR for location {loc_id}: "
                f"{exc}"
            )
            pipeline_errors.append(error_msg)
            pipeline_failure_entries.append({
                "category": "SCHEDULING",
                "severity": "error",
                "source": "scheduling.graph._load_initial_state",
                "message": error_msg,
                "detail": {
                    "location_id": loc_id,
                    "missing_date": exc.missing_date.isoformat(),
                },
            })
            # Skip this location entirely — drop it from the selection set so
            # the locations query below doesn't include it.
            selected_location_ids.discard(loc_id)
            continue

        # Fuse the per-date templates into a single day-name-keyed
        # weekly_schedule. Because the schedule window contains at most one
        # date per day-of-week (num_days <= 7 by convention), each day-name
        # slot comes from whichever template the resolver chose for that date.
        fused_weekly: Dict[str, List[Dict[str, Any]]] = {}
        # Track which templates contributed so we can record their ids in the
        # fused header (used for diagnostics and id_to_name fallback).
        contributing_ids: List[str] = []
        contributing_names: List[str] = []
        for d in week_dates:
            tmpl = per_date_templates[d]
            tmpl_id = str(tmpl.id)
            if tmpl_id not in contributing_ids:
                contributing_ids.append(tmpl_id)
                contributing_names.append(tmpl.name)
            day_name = _DAY_NAMES[d.weekday()]
            slots = _normalize_template_slots_for_dow(
                tmpl.weekly_schedule, d.weekday(), role_map,
            )
            # If multiple dates in the window resolve to the same day-name
            # (e.g. num_days > 7), the later date wins. This matches existing
            # day-name-keyed semantics and is the same constraint pre-existing
            # downstream code already assumed.
            fused_weekly[day_name] = slots

        shift_templates[loc_id] = {
            "id": contributing_ids[0] if contributing_ids else "",
            "name": contributing_names[0] if contributing_names else "",
            "location_id": loc_id,
            "weekly_schedule": fused_weekly,
            # Diagnostic: list of distinct ShiftTemplate ids that contributed
            # to the fused schedule for this location across the window.
            "contributing_template_ids": contributing_ids,
        }

    # Load locations — only those that have a successfully resolved template
    loc_query = select(Location).where(Location.company_id == company_id)
    if selected_location_ids:
        loc_query = loc_query.where(Location.id.in_(selected_location_ids))
    loc_result = await db.execute(loc_query)
    locations_orm = loc_result.scalars().all()
    locations: List[Dict[str, Any]] = [
        {
            "id": str(loc.id),
            "name": loc.name,
            "timezone": loc.timezone,
            "address": loc.address,
            "min_rest_hours": loc.min_rest_hours,
        }
        for loc in locations_orm
    ]

    # Load employees
    emp_result = await db.execute(
        select(Employee).where(Employee.company_id == company_id)
    )
    employees_orm = emp_result.scalars().all()

    # Load employee roles
    er_result = await db.execute(
        select(EmployeeRole).where(EmployeeRole.company_id == company_id)
    )
    emp_roles_orm = er_result.scalars().all()
    emp_roles_map: Dict[str, List[Dict[str, Any]]] = {}
    for er in emp_roles_orm:
        eid = str(er.employee_id)
        if eid not in emp_roles_map:
            emp_roles_map[eid] = []
        emp_roles_map[eid].append({
            "role_id": str(er.role_id),
            "role_name": role_map.get(str(er.role_id), ""),
            "skill_level": er.skill_level,
        })

    # Load employee affinities
    aff_result = await db.execute(
        select(EmployeeAffinity).where(EmployeeAffinity.company_id == company_id)
    )
    affinities_orm = aff_result.scalars().all()
    emp_affinities_map: Dict[str, List[Dict[str, Any]]] = {}
    for aff in affinities_orm:
        eid = str(aff.employee_id)
        if eid not in emp_affinities_map:
            emp_affinities_map[eid] = []
        emp_affinities_map[eid].append({
            "target_id": str(aff.target_employee_id),
            "level": float(aff.level),
        })

    # Load employee availability for the relevant week, minus hours already
    # committed to a Shift row (see _load_employee_availability).
    emp_avail_map = await _load_employee_availability(
        db, company_id, week_start_date, num_days,
        all_employee_ids={str(emp.id) for emp in employees_orm},
    )

    # Load day blackouts (recurring per-day-of-week time ranges during which
    # an employee must NOT be scheduled, e.g. "no work Mon 20:00-22:00").
    DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    blackout_result = await db.execute(
        select(EmployeeDayBlackout).where(
            EmployeeDayBlackout.company_id == company_id
        )
    )
    emp_blackout_map: Dict[str, List[Dict[str, str]]] = {}
    for bo in blackout_result.scalars().all():
        eid = str(bo.employee_id)
        dow = int(bo.day_of_week)
        if not (0 <= dow <= 6):
            continue
        emp_blackout_map.setdefault(eid, []).append({
            "day": DAY_NAMES[dow],
            "start": bo.start_time,
            "end": bo.end_time,
        })

    # Load per-employee scheduling preferences (day, hour-range, hour-range
    # weekly caps). Weights are cast to float since the column is Numeric
    # and SQLAlchemy returns Decimal, which would break arithmetic against
    # the plain floats used elsewhere in the scoring code.
    day_pref_result = await db.execute(
        select(EmployeeDayPreference).where(
            EmployeeDayPreference.company_id == company_id
        )
    )
    emp_day_prefs_map: Dict[str, List[Dict[str, Any]]] = {}
    for dp in day_pref_result.scalars().all():
        emp_day_prefs_map.setdefault(str(dp.employee_id), []).append({
            "day_of_week": dp.day_of_week,
            "weight": float(dp.weight),
        })

    range_pref_result = await db.execute(
        select(EmployeeHourRangePreference).where(
            EmployeeHourRangePreference.company_id == company_id
        )
    )
    emp_range_prefs_map: Dict[str, List[Dict[str, Any]]] = {}
    for rp in range_pref_result.scalars().all():
        emp_range_prefs_map.setdefault(str(rp.employee_id), []).append({
            "start_time": rp.start_time,
            "end_time": rp.end_time,
            "weight": float(rp.weight),
        })

    range_cap_result = await db.execute(
        select(EmployeeHourRangeCap).where(
            EmployeeHourRangeCap.company_id == company_id
        )
    )
    emp_range_caps_map: Dict[str, List[Dict[str, Any]]] = {}
    for rc in range_cap_result.scalars().all():
        emp_range_caps_map.setdefault(str(rc.employee_id), []).append({
            "start_time": rc.start_time,
            "end_time": rc.end_time,
            "max_per_week": rc.max_per_week,
            "weight": float(rc.weight),
        })

    # Build employee dicts, expanding roles to include condensed roles
    employees: List[Dict[str, Any]] = []
    for emp in employees_orm:
        eid = str(emp.id)
        direct_roles = emp_roles_map.get(eid, [])

        # Expand: for each direct role, add any condensed roles it belongs to
        seen_role_ids: set[str] = {r["role_id"] for r in direct_roles}
        expanded_roles = list(direct_roles)
        for role_entry in direct_roles:
            rid = role_entry["role_id"]
            for cid in role_to_condensed.get(rid, []):
                if cid not in seen_role_ids:
                    seen_role_ids.add(cid)
                    expanded_roles.append({
                        "role_id": cid,
                        "role_name": condensed_role_name_map.get(cid, ""),
                        "skill_level": role_entry["skill_level"],
                    })

        employees.append({
            "id": eid,
            "full_name": emp.full_name,
            "email": emp.email,
            "location_ids": [str(lid) for lid in (emp.location_ids or [])],
            "roles": expanded_roles,
            "affinities": emp_affinities_map.get(eid, []),
            "available_windows": emp_avail_map.get(eid, []),
            "max_hours_per_week": emp.max_hours_per_week,
            "day_blackouts": emp_blackout_map.get(eid, []),
            "day_preferences": emp_day_prefs_map.get(eid, []),
            "hour_range_preferences": emp_range_prefs_map.get(eid, []),
            "hour_range_caps": emp_range_caps_map.get(eid, []),
        })

    # Per-employee preferences, duplicated out of `employees` in the shape
    # validate_and_update_availability's cap-trimming pass expects (see
    # SchedulingState.employee_preferences).
    employee_preferences: Dict[str, Dict[str, Any]] = {
        e["id"]: {
            "day_preferences": e["day_preferences"],
            "hour_range_preferences": e["hour_range_preferences"],
            "hour_range_caps": e["hour_range_caps"],
        }
        for e in employees
    }

    initial_state: Dict[str, Any] = {
        "company_id": company_id,
        "week_start_date": week_start_date,
        "locations": locations,
        "shift_templates": shift_templates,
        "employees": employees,
        "availability_draft": {},
        "employee_weekly_hours_draft": {},
        "range_counts_draft": {},
        "employee_preferences": employee_preferences,
        "current_location_index": 0,
        "completed_location_ids": [],
        "retry_count": 0,
        "draft_schedules": [],
        "errors": list(pipeline_errors),
        "current_prompt": "",
        "current_raw_response": "",
        "current_parsed_shifts": [],
        "conflict_notes": "",
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "current_location": {},
        "current_shift_template": {},
        "current_employees": [],
        "failure_entries": list(pipeline_failure_entries),
        "role_equivalents": {k: list(v) for k, v in role_equivalents.items()},
        "num_days": num_days,
    }

    return initial_state


async def _load_role_history_minutes(
    db: AsyncSession,
    company_id: str,
    week_start_date: str,
) -> Dict[tuple, float]:
    """Load worked minutes per (employee_id, role_name) for the past 3 months.

    Derived from APPROVED `Shift` rows rather than read from the
    `employee_role_minutes` aggregate (#97).

    The aggregate is written once, at approval. Editing an approved schedule
    never adjusted it, so deleting a shift left its minutes booked,
    reassigning one left them credited to the previous employee, and
    changing the times left the original duration standing. Nothing
    reconciled any of it, so every edit nudged this fairness history away
    from reality — silently, permanently, and with no signal to the manager
    doing the editing.

    Computing from the rows removes the class of problem rather than
    patching one instance, the same shape #84 stage 1 gave availability
    holds in `_committed_shifts_by_employee`: a delete releases its minutes,
    a reassignment moves them, and a time change re-measures, all with no
    bookkeeping that can fall out of step.

    `employee_role_minutes` is still written at approval and is deliberately
    left in place — it is simply no longer what scheduling reads. See the
    note at its write site in routers/schedules.py.
    """
    from datetime import date as date_type

    ref_date = date_type.fromisoformat(week_start_date)
    # Go back ~3 months, to the first of that month. Matches the window the
    # aggregate covered, which keyed on month_start.
    three_months_ago = ref_date.replace(day=1)
    for _ in range(3):
        three_months_ago = (three_months_ago - timedelta(days=1)).replace(day=1)

    # Only approved schedules count. A draft is a proposal and a rejected one
    # was explicitly thrown away; neither is time anybody worked.
    rows = (await db.execute(
        select(
            Shift.employee_id,
            Shift.role_id,
            Shift.start_time,
            Shift.end_time,
        )
        .join(ShiftSchedule, Shift.shift_schedule_id == ShiftSchedule.id)
        .where(
            Shift.company_id == company_id,
            Shift.date >= three_months_ago,
            ShiftSchedule.status == "approved",
        )
    )).all()

    # Resolve role_id -> role_name. Deliberately via the roles table rather
    # than Shift.role_name, which is denormalized at write time: a renamed
    # role should follow its history, not split it in two.
    role_result = await db.execute(
        select(Role).where(Role.company_id == company_id)
    )
    role_map = {str(r.id): r.name for r in role_result.scalars().all()}

    history: Dict[tuple, float] = {}
    for employee_id, role_id, start_time, end_time in rows:
        role_name = role_map.get(str(role_id), "")
        if not role_name:
            continue
        if start_time is None or end_time is None:
            continue
        minutes = (end_time - start_time).total_seconds() / 60.0
        if minutes <= 0:
            continue
        key = (str(employee_id), role_name)
        history[key] = history.get(key, 0.0) + minutes

    return history


async def run_scheduling_pipeline(
    company_id: str,
    week_start_date: str,
    db: AsyncSession,
    template_ids: List[str] | None = None,
    use_local: bool = False,
    strategy: Strategy = "random",
    strategy_param: float = 0.5,
    strategy_param2: float = 0.0,
    num_days: int = 7,
) -> AsyncGenerator[LocationResult, None]:
    """Run the scheduling pipeline and yield LocationResult dicts as they're produced.

    Args:
        company_id: UUID of the company to schedule for.
        week_start_date: Start date of the week in "YYYY-MM-DD" format.
        db: An active async database session.
        template_ids: Optional list of shift template UUIDs to generate for.
        use_local: If True, use the local algorithmic scheduler instead of
            the LLM.
        strategy: Scheduling strategy for the local scheduler ("random",
            "rotation", or "rotation_history").
        strategy_param: Fairness weight for rotation_history (0.0-1.0).
        num_days: Number of days to schedule starting from week_start_date.

    Yields:
        LocationResult dicts, one per location, as each location completes.
    """
    initial_state = await _load_initial_state(
        company_id, week_start_date, db, template_ids=template_ids,
        num_days=num_days,
    )

    if not initial_state["locations"]:
        return

    # Free-plan allowance, applied per location (services/location_quota.py).
    # Applied HERE rather than in the route because this is where the
    # location set is resolved: a request may name locations, name templates
    # that imply locations, or name nothing at all and mean every location.
    #
    # A blocked location is reported and skipped rather than failing the
    # whole run, matching how PARSE_ERROR and CONFLICT are handled. A free
    # tenant whose first location is spent and whose second is not should
    # get the second one scheduled, not a refusal for both.
    from backend.services.location_quota import resolve_location_quota

    quota = await resolve_location_quota(
        db,
        company_id,
        [loc["id"] for loc in initial_state["locations"]],
        week_start_date,
    )
    allowed_locations = []
    for loc in initial_state["locations"]:
        verdict = quota.get(loc["id"])
        if verdict is None or verdict["allowed"]:
            allowed_locations.append(loc)
            continue
        yield LocationResult(
            location_id=loc["id"],
            location_name=loc["name"],
            shifts=[],
            errors=[verdict["message"] or "Free plan allowance used."],
            status="QUOTA_EXCEEDED",
        )

    if not allowed_locations:
        return
    initial_state["locations"] = allowed_locations

    # For rotation_history strategy, load 3-month role minutes from DB
    if use_local and strategy == "rotation_history":
        initial_state["_role_history_minutes"] = await _load_role_history_minutes(
            db, company_id, week_start_date,
        )

    graph = build_scheduling_graph(use_local=use_local, strategy=strategy, strategy_param=strategy_param, strategy_param2=strategy_param2)
    compiled = graph.compile()

    # Track how many results we've already yielded
    yielded_count = 0
    final_input_tokens = 0
    final_output_tokens = 0
    accumulated_failures: List[Dict[str, Any]] = []

    # Stream through the graph execution
    async for state_update in compiled.astream(initial_state):
        # LangGraph astream yields {node_name: state_update} dicts
        for _node_name, node_state in state_update.items():
            if isinstance(node_state, dict):
                if "total_input_tokens" in node_state:
                    final_input_tokens = node_state["total_input_tokens"]
                    final_output_tokens = node_state["total_output_tokens"]
                if "failure_entries" in node_state:
                    accumulated_failures = node_state["failure_entries"]
                if "draft_schedules" in node_state:
                    draft_schedules = node_state["draft_schedules"]
                    while yielded_count < len(draft_schedules):
                        yield draft_schedules[yielded_count]
                        yielded_count += 1

    # Persist any accumulated failure entries
    if accumulated_failures:
        from backend.services.failure_logger import log_failure_batch

        # Attach company_id to each entry
        for entry in accumulated_failures:
            entry["company_id"] = company_id
        await log_failure_batch(accumulated_failures)

    # Record token usage and calculate billing
    if final_input_tokens or final_output_tokens:
        from backend.services.billing import check_and_record_usage, deduct_credits_for_overage

        billing_result = await check_and_record_usage(
            db, company_id, final_input_tokens, final_output_tokens,
        )
        # Deduct purchased credits for any overage
        if billing_result.get("charged_usd", 0) > 0:
            await deduct_credits_for_overage(
                db, company_id, billing_result["charged_usd"],
            )
