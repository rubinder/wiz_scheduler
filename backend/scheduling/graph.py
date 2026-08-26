from datetime import date as date_type, datetime, timedelta, timezone
from typing import Any, AsyncGenerator, Dict, List

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
    ShiftTemplate,
)
from backend.scheduling.local_scheduler import Strategy, local_schedule
from backend.scheduling.nodes import (
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
# It also bounds the free plan. FREE_PLAN_MAX_SCHEDULES_PER_MONTH counts
# generations, not days, so an unbounded window would let 2 generations cover
# a year.
MAX_SCHEDULE_DAYS = 7


def _validate_num_days(num_days: int) -> None:
    """Raise ValueError if *num_days* is outside 1..MAX_SCHEDULE_DAYS."""
    if not 1 <= num_days <= MAX_SCHEDULE_DAYS:
        raise ValueError(
            f"num_days must be between 1 and {MAX_SCHEDULE_DAYS} "
            f"(one calendar week); got {num_days}"
        )


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

    # Load employee availability for the relevant week
    week_start = datetime.strptime(week_start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    week_end = week_start + timedelta(days=num_days)

    avail_result = await db.execute(
        select(EmployeeAvailability).where(
            EmployeeAvailability.company_id == company_id,
            EmployeeAvailability.start_time >= week_start,
            EmployeeAvailability.start_time < week_end,
        )
    )
    avail_orm = avail_result.scalars().all()
    emp_avail_map: Dict[str, List[Dict[str, str]]] = {}
    for av in avail_orm:
        eid = str(av.employee_id)
        if eid not in emp_avail_map:
            emp_avail_map[eid] = []

        start_dt = av.start_time
        end_dt = av.end_time

        # Fix midnight end times: if end <= start, the end_time was stored as
        # 00:00:00 on the same date (meaning "end of day"), so bump it to 23:59.
        if start_dt and end_dt and end_dt <= start_dt:
            end_dt = start_dt.replace(hour=23, minute=59, second=0, microsecond=0)

        emp_avail_map[eid].append({
            "start": start_dt.isoformat() if hasattr(start_dt, "isoformat") else str(start_dt),
            "end": end_dt.isoformat() if hasattr(end_dt, "isoformat") else str(end_dt),
        })

    # Employees with NO availability records are treated as "not available".
    # They get an empty availability list so the LLM and validator will not
    # schedule them until explicit availability is provided.
    all_emp_ids = {str(emp.id) for emp in employees_orm}
    emps_with_avail = set(emp_avail_map.keys())
    for eid in all_emp_ids - emps_with_avail:
        emp_avail_map[eid] = []

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

    initial_state: Dict[str, Any] = {
        "company_id": company_id,
        "week_start_date": week_start_date,
        "locations": locations,
        "shift_templates": shift_templates,
        "employees": employees,
        "availability_draft": {},
        "employee_weekly_hours_draft": {},
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
    """Load employee role minutes for the past 3 months.

    Returns a dict of (employee_id, role_name) -> total_minutes.
    """
    from datetime import date as date_type
    from backend.models.employee_role_minutes import EmployeeRoleMinutes

    ref_date = date_type.fromisoformat(week_start_date)
    # Go back ~3 months
    three_months_ago = ref_date.replace(day=1)
    for _ in range(3):
        three_months_ago = (three_months_ago - timedelta(days=1)).replace(day=1)

    result = await db.execute(
        select(EmployeeRoleMinutes).where(
            EmployeeRoleMinutes.company_id == company_id,
            EmployeeRoleMinutes.month_start >= three_months_ago,
        )
    )
    rows = result.scalars().all()

    # We need role_id -> role_name mapping to key by role_name (which is what
    # the local scheduler uses).
    role_result = await db.execute(
        select(Role).where(Role.company_id == company_id)
    )
    role_map = {str(r.id): r.name for r in role_result.scalars().all()}

    history: Dict[tuple, float] = {}
    for row in rows:
        role_name = role_map.get(str(row.role_id), "")
        if not role_name:
            continue
        key = (str(row.employee_id), role_name)
        history[key] = history.get(key, 0.0) + row.total_minutes

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
