from datetime import datetime
from typing import Any, AsyncGenerator, Dict, List

from langgraph.graph import END, StateGraph
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import (
    Company,
    Employee,
    EmployeeAffinity,
    EmployeeAvailability,
    EmployeeRole,
    Location,
    Role,
    ShiftTemplate,
    TokenUsage,
)
from backend.scheduling.nodes import (
    build_prompt,
    call_llm,
    emit_result,
    load_location_context,
    parse_schedule,
    validate_and_update_availability,
)
from backend.scheduling.state import LocationResult, SchedulingState


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


def build_scheduling_graph() -> StateGraph:
    """Construct the LangGraph state graph for the scheduling pipeline."""
    graph = StateGraph(SchedulingState)

    # Add nodes
    graph.add_node("load_location_context", load_location_context)
    graph.add_node("build_prompt", build_prompt)
    graph.add_node("call_llm", call_llm)
    graph.add_node("parse_schedule", parse_schedule)
    graph.add_node("validate_and_update_availability", validate_and_update_availability)
    graph.add_node("emit_result", emit_result)

    # Set entry point
    graph.set_entry_point("load_location_context")

    # Linear edges
    graph.add_edge("load_location_context", "build_prompt")
    graph.add_edge("build_prompt", "call_llm")
    graph.add_edge("call_llm", "parse_schedule")
    graph.add_edge("parse_schedule", "validate_and_update_availability")

    # Conditional edge after validation: retry or emit
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


async def _load_initial_state(
    company_id: str,
    week_start_date: str,
    db: AsyncSession,
    template_ids: List[str] | None = None,
) -> Dict[str, Any]:
    """Query the database to build the initial SchedulingState."""

    # Load roles for name lookup
    role_result = await db.execute(
        select(Role).where(Role.company_id == company_id)
    )
    roles_orm = role_result.scalars().all()
    role_map: Dict[str, str] = {str(r.id): r.name for r in roles_orm}

    # Load shift templates, optionally filtered by template_ids
    st_query = select(ShiftTemplate).where(ShiftTemplate.company_id == company_id)
    if template_ids:
        st_query = st_query.where(ShiftTemplate.id.in_(template_ids))
    st_result = await db.execute(st_query)
    templates_orm = st_result.scalars().all()

    shift_templates: Dict[str, Dict[str, Any]] = {}
    selected_location_ids: set[str] = set()
    for tmpl in templates_orm:
        loc_id = str(tmpl.location_id)
        selected_location_ids.add(loc_id)
        weekly_schedule_raw = tmpl.weekly_schedule or {}

        # Normalize weekly_schedule to {day: [slots]} dict.
        # It may be stored as either:
        #   - a dict keyed by day name: {"Monday": [{...}, ...], ...}
        #   - a flat list with "day" field:  [{"day": "Monday", ...}, ...]
        if isinstance(weekly_schedule_raw, list):
            weekly_schedule: Dict[str, List[Dict[str, Any]]] = {}
            for slot in weekly_schedule_raw:
                day = slot.get("day", "")
                if day:
                    weekly_schedule.setdefault(day, []).append(slot)
        else:
            weekly_schedule = dict(weekly_schedule_raw)

        # Enrich weekly_schedule slots with role_name from the role lookup
        enriched_schedule: Dict[str, List[Dict[str, Any]]] = {}
        for day, slots in weekly_schedule.items():
            enriched_slots: List[Dict[str, Any]] = []
            for slot in slots:
                enriched_slot = dict(slot)
                rid = enriched_slot.get("role_id", "")
                if rid and rid in role_map and "role_name" not in enriched_slot:
                    enriched_slot["role_name"] = role_map[rid]
                enriched_slots.append(enriched_slot)
            enriched_schedule[day] = enriched_slots

        shift_templates[loc_id] = {
            "id": str(tmpl.id),
            "name": tmpl.name,
            "location_id": loc_id,
            "weekly_schedule": enriched_schedule,
        }

    # Load locations — only those that have a selected template
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
    # Parse the week_start_date to filter availability
    from datetime import datetime, timedelta

    week_start = datetime.strptime(week_start_date, "%Y-%m-%d")
    week_end = week_start + timedelta(days=7)

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
        emp_avail_map[eid].append({
            "start": av.start_time.isoformat() if hasattr(av.start_time, "isoformat") else str(av.start_time),
            "end": av.end_time.isoformat() if hasattr(av.end_time, "isoformat") else str(av.end_time),
        })

    # Build employee dicts
    employees: List[Dict[str, Any]] = []
    for emp in employees_orm:
        eid = str(emp.id)
        employees.append({
            "id": eid,
            "full_name": emp.full_name,
            "email": emp.email,
            "location_ids": [str(lid) for lid in (emp.location_ids or [])],
            "roles": emp_roles_map.get(eid, []),
            "affinities": emp_affinities_map.get(eid, []),
            "available_windows": emp_avail_map.get(eid, []),
        })

    initial_state: Dict[str, Any] = {
        "company_id": company_id,
        "week_start_date": week_start_date,
        "locations": locations,
        "shift_templates": shift_templates,
        "employees": employees,
        "availability_draft": {},
        "current_location_index": 0,
        "completed_location_ids": [],
        "retry_count": 0,
        "draft_schedules": [],
        "errors": [],
        "current_prompt": "",
        "current_raw_response": "",
        "current_parsed_shifts": [],
        "conflict_notes": "",
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "current_location": {},
        "current_shift_template": {},
        "current_employees": [],
    }

    return initial_state


async def run_scheduling_pipeline(
    company_id: str,
    week_start_date: str,
    db: AsyncSession,
    template_ids: List[str] | None = None,
) -> AsyncGenerator[LocationResult, None]:
    """Run the scheduling pipeline and yield LocationResult dicts as they're produced.

    Args:
        company_id: UUID of the company to schedule for.
        week_start_date: Start date of the week in "YYYY-MM-DD" format.
        db: An active async database session.
        template_ids: Optional list of shift template UUIDs to generate for.

    Yields:
        LocationResult dicts, one per location, as each location completes.
    """
    initial_state = await _load_initial_state(
        company_id, week_start_date, db, template_ids=template_ids
    )

    if not initial_state["locations"]:
        return

    graph = build_scheduling_graph()
    compiled = graph.compile()

    # Track how many results we've already yielded
    yielded_count = 0
    final_input_tokens = 0
    final_output_tokens = 0

    # Stream through the graph execution
    async for state_update in compiled.astream(initial_state):
        # LangGraph astream yields {node_name: state_update} dicts
        for _node_name, node_state in state_update.items():
            if isinstance(node_state, dict):
                if "total_input_tokens" in node_state:
                    final_input_tokens = node_state["total_input_tokens"]
                    final_output_tokens = node_state["total_output_tokens"]
                if "draft_schedules" in node_state:
                    draft_schedules = node_state["draft_schedules"]
                    while yielded_count < len(draft_schedules):
                        yield draft_schedules[yielded_count]
                        yielded_count += 1

    # Upsert token usage for the ownership group
    if final_input_tokens or final_output_tokens:
        await _update_token_usage(
            db, company_id, final_input_tokens, final_output_tokens
        )


async def _update_token_usage(
    db: AsyncSession,
    company_id: str,
    input_tokens: int,
    output_tokens: int,
) -> None:
    """Upsert the TokenUsage row for the company's ownership group and current month."""
    result = await db.execute(
        select(Company).where(Company.id == company_id)
    )
    company = result.scalar_one_or_none()
    if not company or not company.ownership_group_id:
        return

    now = datetime.utcnow()
    ownership_group_id = company.ownership_group_id

    result = await db.execute(
        select(TokenUsage).where(
            TokenUsage.ownership_group_id == ownership_group_id,
            TokenUsage.year == now.year,
            TokenUsage.month == now.month,
        )
    )
    usage = result.scalar_one_or_none()

    if usage:
        usage.input_tokens += input_tokens
        usage.output_tokens += output_tokens
        usage.total_tokens += input_tokens + output_tokens
        usage.updated_at = now
    else:
        usage = TokenUsage(
            ownership_group_id=ownership_group_id,
            year=now.year,
            month=now.month,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
        )
        db.add(usage)

    await db.flush()
