from typing import Any, Dict, List


def _format_role_requirements(shift_template: Dict[str, Any]) -> str:
    """Format the weekly_schedule from a shift template into role requirement lines.

    Expected shift_template["weekly_schedule"] structure:
    {
        "Monday": [
            {"role_name": "Barista", "role_id": "...", "headcount": 2,
             "start_time": "07:00", "end_time": "15:00"},
            ...
        ],
        ...
    }
    """
    lines: List[str] = []
    weekly_schedule: Dict[str, List[dict]] = shift_template.get("weekly_schedule", {})
    for day, slots in weekly_schedule.items():
        for slot in slots:
            role_name = slot.get("role_name", "Unknown")
            headcount = slot.get("headcount", 1)
            start = slot.get("start_time", "??:??")
            end = slot.get("end_time", "??:??")
            lines.append(f"{day} | {role_name} | {headcount} | {start}-{end}")
    return "\n".join(lines)


def _format_employees(employees: List[Dict[str, Any]]) -> str:
    """Format a list of employee dicts into a readable roster block.

    Each employee dict is expected to contain:
        id, full_name, roles, affinities, available_windows
    """
    blocks: List[str] = []
    for emp in employees:
        emp_id = emp.get("id", "")
        name = emp.get("full_name", "")
        roles = emp.get("roles", [])
        affinities = emp.get("affinities", [])
        windows = emp.get("available_windows", [])

        roles_str = ", ".join(
            f'{{"role_name": "{r.get("role_name", "")}", "skill_level": {r.get("skill_level", 0)}}}'
            for r in roles
        )
        affinities_str = ", ".join(
            f'{{"target_id": "{a.get("target_id", "")}", "level": {a.get("level", 0)}}}'
            for a in affinities
        )
        windows_str = ", ".join(
            f'{{"start": "{w.get("start", "")}", "end": "{w.get("end", "")}"}}'
            for w in windows
        )

        block = (
            f"- id: {emp_id}\n"
            f"  name: {name}\n"
            f"  roles: [{roles_str}]\n"
            f"  affinities: [{affinities_str}]\n"
            f"  available_windows: [{windows_str}]"
        )
        blocks.append(block)
    return "\n".join(blocks)


def build_schedule_prompt(
    location: Dict[str, Any],
    shift_template: Dict[str, Any],
    employees: List[Dict[str, Any]],
    week_start_date: str,
    conflict_notes: str = "",
) -> str:
    """Build the full scheduling prompt for a single location."""
    role_requirements = _format_role_requirements(shift_template)
    employee_list = _format_employees(employees)

    conflict_section = ""
    if conflict_notes:
        conflict_section = (
            f"PREVIOUS CONFLICTS\n"
            f"==================\n"
            f"{conflict_notes}\n\n"
        )

    prompt = (
        f"You are a scheduling assistant for {location['name']} ({location['timezone']}).\n"
        f"\n"
        f"SHIFT REQUIREMENTS\n"
        f"==================\n"
        f"{role_requirements}\n"
        f"Format per line: Day | Role Name | Required headcount | Time range\n"
        f"\n"
        f"EMPLOYEE ROSTER\n"
        f"===============\n"
        f"{employee_list}\n"
        f"Each entry: id, name, roles ([{{role_name, skill_level}}]),\n"
        f"affinities ([{{target_id, level}}]),\n"
        f"available_windows (pre-filtered).\n"
        f"\n"
        f"AFFINITY RULES\n"
        f"==============\n"
        f"level =  1.0  \u2192 MUST schedule together on every shared shift (hard constraint)\n"
        f"level = -1.0  \u2192 MUST NOT share any shift (hard constraint)\n"
        f"|level| < 1   \u2192 soft preference; best effort\n"
        f"\n"
        f"{conflict_section}"
        f"INSTRUCTIONS\n"
        f"============\n"
        f"1. Schedule the week of {week_start_date} using only days and roles in SHIFT REQUIREMENTS.\n"
        f"2. Only assign employees to roles listed in their \"roles\" array.\n"
        f"3. Only schedule employees within their available_windows.\n"
        f"4. Honour all hard affinity constraints. Optimise for soft ones.\n"
        f"5. Distribute hours fairly among employees of the same role.\n"
        f"6. Prefer higher skill_level employees for demanding roles.\n"
        f"\n"
        f"OUTPUT FORMAT\n"
        f"=============\n"
        f"Return ONLY a valid JSON array \u2014 no prose, no markdown, no code fences.\n"
        f"Each element:\n"
        f"{{\n"
        f"  \"employee_id\": \"<uuid>\",\n"
        f"  \"employee_name\": \"<name>\",\n"
        f"  \"role_name\": \"<role name from shift template>\",\n"
        f"  \"date\": \"YYYY-MM-DD\",\n"
        f"  \"start_time\": \"YYYY-MM-DDTHH:MM:SS+HH:MM\",\n"
        f"  \"end_time\":   \"YYYY-MM-DDTHH:MM:SS+HH:MM\"\n"
        f"}}"
    )
    return prompt
