"""Preferences reach the AI path two ways.

Hard weights are already handled: prompts.eligible_for_slot filters them, so
the model is never offered the candidate. Soft weights order the Eligible list
so the model reads better candidates first.

The frequency cap is the one parameter that cannot be pre-filtered here — a
whole week generates in a single call, so per-slot counts do not exist
beforehand. It is enforced after generation instead.
"""

from backend.scheduling.nodes import validate_and_update_availability
from backend.scheduling.prompts import build_schedule_prompt


def _shift(eid, date, start, end, status="ok"):
    return {
        "employee_id": eid,
        "employee_name": eid,
        "role_id": "r1",
        "role_name": "Cook",
        "location_id": "loc1",
        "date": date,
        "start_time": f"{date}T{start}:00-04:00",
        "end_time": f"{date}T{end}:00-04:00",
        "status": status,
    }


def test_cap_violations_beyond_the_allowance_are_vacated():
    """Four 16:00-22:00 shifts against a cap of 3 -> the fourth is VACANT.

    `employees` is included (with the matching hour_range_caps entry) so
    _grow_range_counts actually runs and range_counts_draft is populated --
    without it this test would only prove the VACANT statuses, and say
    nothing about the trim/count interaction the cap-trimming feature is
    for.
    """
    shifts = [
        _shift("e1", "2026-08-31", "16:00", "22:00"),
        _shift("e1", "2026-09-01", "16:00", "22:00"),
        _shift("e1", "2026-09-02", "16:00", "22:00"),
        _shift("e1", "2026-09-03", "16:00", "22:00"),
    ]
    caps = [
        {"start_time": "16:00", "end_time": "22:00",
         "max_per_week": 3, "weight": 1.0}
    ]
    state = {
        "current_parsed_shifts": shifts,
        "availability_draft": {},
        "retry_count": 1,
        "conflict_notes": "",
        "employee_weekly_hours_draft": {},
        "employees": [{"id": "e1", "hour_range_caps": caps}],
        "employee_preferences": {
            "e1": {
                "day_preferences": [],
                "hour_range_preferences": [],
                "hour_range_caps": caps,
            }
        },
    }
    out = validate_and_update_availability(state)
    statuses = [s["status"] for s in out["current_parsed_shifts"]]
    assert statuses.count("VACANT") == 1
    assert statuses[:3] == ["ok", "ok", "ok"]
    # The vacated 4th shift must not itself be counted: exactly 3, not 4.
    assert out["range_counts_draft"][("e1", "16:00", "22:00")] == 3


def test_a_cap_within_its_allowance_vacates_nothing():
    shifts = [
        _shift("e1", "2026-08-31", "16:00", "22:00"),
        _shift("e1", "2026-09-01", "16:00", "22:00"),
    ]
    state = {
        "current_parsed_shifts": shifts,
        "availability_draft": {},
        "retry_count": 1,
        "conflict_notes": "",
        "employee_weekly_hours_draft": {},
        "employee_preferences": {
            "e1": {
                "day_preferences": [],
                "hour_range_preferences": [],
                "hour_range_caps": [
                    {"start_time": "16:00", "end_time": "22:00",
                     "max_per_week": 3, "weight": 1.0}
                ],
            }
        },
    }
    out = validate_and_update_availability(state)
    assert all(s["status"] == "ok" for s in out["current_parsed_shifts"])


def test_overlapping_caps_dont_leak_counts_from_a_vacated_shift():
    """A later, narrower cap vacating a shift must not permanently inflate
    an earlier, broader cap's count for a shift that was never worked.

    Caps: A = 16:00-22:00 max 3 (listed first), B = 18:00-20:00 max 1
    (listed second, and nested inside A's range so any 18:00-20:00 shift
    matches both).

    Shifts in date order:
      1. 18:00-20:00 -- matches A and B. Neither at its limit -> stays ok.
         (A=1, B=1)
      2. 18:00-20:00 -- matches A and B. A would be fine (2<=3) but B would
         be exceeded (2>1) -> VACATED. Because this shift is never worked,
         neither A nor B should count it: A must stay at 1, not jump to 2.
      3. 16:00-18:00 -- matches only A (no overlap with B). A=1+1=2<=3 ->
         stays ok. (A=2)
      4. 16:00-18:00 -- matches only A. Under the old (buggy) code, shift 2
         had already inflated A's count to 2, so this shift would wrongly
         read A=3 and get vacated one slot early -- an employee legitimately
         entitled to a 3rd A-shift this week is refused it. With counts
         reflecting only shifts actually worked, A's real count going into
         this shift is 2 (shifts 1 and 3), so this is A's 3rd occurrence
         (2+1=3<=3) and it must stay ok.
    """
    shifts = [
        _shift("e1", "2026-08-31", "18:00", "20:00"),
        _shift("e1", "2026-09-01", "18:00", "20:00"),
        _shift("e1", "2026-09-02", "16:00", "18:00"),
        _shift("e1", "2026-09-03", "16:00", "18:00"),
    ]
    caps = [
        {"start_time": "16:00", "end_time": "22:00",
         "max_per_week": 3, "weight": 1.0},
        {"start_time": "18:00", "end_time": "20:00",
         "max_per_week": 1, "weight": 1.0},
    ]
    state = {
        "current_parsed_shifts": shifts,
        "availability_draft": {},
        "retry_count": 1,
        "conflict_notes": "",
        "employee_weekly_hours_draft": {},
        "employees": [{"id": "e1", "hour_range_caps": caps}],
        "employee_preferences": {
            "e1": {
                "day_preferences": [],
                "hour_range_preferences": [],
                "hour_range_caps": caps,
            }
        },
    }
    out = validate_and_update_availability(state)
    statuses = [s["status"] for s in out["current_parsed_shifts"]]
    assert statuses == ["ok", "VACANT", "ok", "ok"]
    # The vacated 2nd shift must contribute to neither cap's count: A's
    # three actually-worked matches are shifts 1, 3, 4; B's one
    # actually-worked match is shift 1.
    assert out["range_counts_draft"][("e1", "16:00", "22:00")] == 3
    assert out["range_counts_draft"][("e1", "18:00", "20:00")] == 1


def test_no_preferences_leaves_shifts_untouched():
    shifts = [_shift("e1", "2026-08-31", "16:00", "22:00")]
    state = {
        "current_parsed_shifts": shifts,
        "availability_draft": {},
        "retry_count": 1,
        "conflict_notes": "",
        "employee_weekly_hours_draft": {},
        "employee_preferences": {},
    }
    out = validate_and_update_availability(state)
    assert out["current_parsed_shifts"][0]["status"] == "ok"


def test_eligible_list_is_sorted_best_first_by_soft_preference():
    """A soft (weight < 1.0) preference reorders the prompt's Eligible list.

    e_soft prefers Tuesday and is scheduled here on Monday, so it takes a
    penalty from preference_score; e_clean has no preferences at all and
    scores 0.0. Both are otherwise identically eligible for the one Monday
    Floor slot. e_soft is placed first in the input roster specifically so
    that only the sort -- not input order or id ordering -- explains the
    rendered order.
    """
    location = {"id": "loc1", "name": "Test Location", "timezone": "UTC"}
    shift_template = {
        "weekly_schedule": {
            "Monday": [
                {"role_name": "Floor", "role_id": "role1",
                 "start_time": "09:00", "end_time": "17:00", "headcount": 1}
            ],
        },
    }
    mon_9_17 = {"start": "2026-08-31T09:00:00+00:00", "end": "2026-08-31T17:00:00+00:00"}
    employees = [
        {
            "id": "e_soft",
            "full_name": "Soft Preference",
            "email": "soft@test.com",
            "location_ids": ["loc1"],
            "roles": [{"role_name": "Floor", "role_id": "role1", "skill_level": 1}],
            "affinities": [],
            "available_windows": [mon_9_17],
            "day_blackouts": [],
            "day_preferences": [{"day_of_week": 1, "weight": 0.5}],  # Tuesday
            "hour_range_preferences": [],
            "hour_range_caps": [],
        },
        {
            "id": "e_clean",
            "full_name": "No Preference",
            "email": "clean@test.com",
            "location_ids": ["loc1"],
            "roles": [{"role_name": "Floor", "role_id": "role1", "skill_level": 1}],
            "affinities": [],
            "available_windows": [mon_9_17],
            "day_blackouts": [],
            "day_preferences": [],
            "hour_range_preferences": [],
            "hour_range_caps": [],
        },
    ]
    prompt = build_schedule_prompt(location, shift_template, employees, "2026-08-31")
    eligible_line = next(
        line for line in prompt.splitlines() if line.strip().startswith("Eligible:")
    )
    assert eligible_line.index("e_clean") < eligible_line.index("e_soft")


def test_cap_seeds_from_range_counts_draft_across_locations():
    """A cap already exhausted by an earlier location trims new shifts too.

    range_counts_draft carries a running count across locations in the same
    graph run (see nodes._grow_range_counts). The trim must start counting
    from that seed, not from zero, or an employee already at their cap from
    an earlier location could still pick up new shifts at this one.
    """
    shifts = [
        _shift("e1", "2026-08-31", "16:00", "22:00"),
        _shift("e1", "2026-09-01", "16:00", "22:00"),
    ]
    state = {
        "current_parsed_shifts": shifts,
        "availability_draft": {},
        "retry_count": 1,
        "conflict_notes": "",
        "employee_weekly_hours_draft": {},
        "range_counts_draft": {("e1", "16:00", "22:00"): 2},
        "employee_preferences": {
            "e1": {
                "day_preferences": [],
                "hour_range_preferences": [],
                "hour_range_caps": [
                    {"start_time": "16:00", "end_time": "22:00",
                     "max_per_week": 2, "weight": 1.0}
                ],
            }
        },
    }
    out = validate_and_update_availability(state)
    statuses = [s["status"] for s in out["current_parsed_shifts"]]
    assert statuses == ["VACANT", "VACANT"]


def test_conflict_shifts_do_not_consume_cap_allowance_or_get_overwritten():
    """A CONFLICT shift is not worked -- it must not burn cap allowance, and
    the trim must never clobber a CONFLICT status back to VACANT.

    This graph never raises; status is the only channel a failure like
    CONFLICT reaches the manager through (emit_result reports the location's
    overall status from the shifts' statuses). Losing a CONFLICT to a
    VACANT overwrite would silently drop that signal.
    """
    shifts = [
        _shift("e1", "2026-08-31", "16:00", "22:00"),
        _shift("e1", "2026-09-01", "16:00", "22:00"),
        _shift("e1", "2026-09-02", "16:00", "22:00"),
    ]
    # A pre-existing consumed window overlapping only the 3rd shift -- what
    # nodes._windows_overlap flags as a conflict below.
    conflicting_window = {
        "start": shifts[2]["start_time"], "end": shifts[2]["end_time"],
    }
    state = {
        "current_parsed_shifts": shifts,
        "availability_draft": {"e1": [conflicting_window]},
        "retry_count": 1,
        "conflict_notes": "",
        "employee_weekly_hours_draft": {},
        "employee_preferences": {
            "e1": {
                "day_preferences": [],
                "hour_range_preferences": [],
                "hour_range_caps": [
                    {"start_time": "16:00", "end_time": "22:00",
                     "max_per_week": 2, "weight": 1.0}
                ],
            }
        },
    }
    out = validate_and_update_availability(state)
    statuses = [s["status"] for s in out["current_parsed_shifts"]]
    assert statuses == ["ok", "ok", "CONFLICT"]
