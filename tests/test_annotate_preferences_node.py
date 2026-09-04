"""annotate_preferences (#99): the post-pass that explains the schedule.

Runs after validate_and_update_availability on both paths. Reports, never
acts: shifts and statuses are untouched, only preference_violations and
the per-location summary are added.
"""

from backend.scheduling.nodes import annotate_preferences, emit_result


def _shift(eid, date, start, end, status="ok", role="Cook"):
    return {
        "employee_id": eid, "employee_name": eid, "role_id": "r1",
        "role_name": role, "location_id": "loc1", "date": date,
        "start_time": f"{date}T{start}:00-04:00",
        "end_time": f"{date}T{end}:00-04:00", "status": status,
    }


def _emp(eid, day_prefs=None, range_prefs=None, caps=None, avail_dates=("2026-09-03",)):
    return {
        "id": eid, "full_name": eid, "location_ids": ["loc1"],
        "roles": [{"role_id": "r1", "role_name": "Cook", "skill_level": 3}],
        "affinities": [],
        "available_windows": [
            {"start": f"{d}T00:00:00+00:00", "end": f"{d}T23:59:00+00:00"} for d in avail_dates
        ],
        "max_hours_per_week": None, "day_blackouts": [],
        "day_preferences": day_prefs or [],
        "hour_range_preferences": range_prefs or [],
        "hour_range_caps": caps or [],
    }


def _state(shifts, employees, availability_draft=None):
    prefs = {
        e["id"]: {
            "day_preferences": e["day_preferences"],
            "hour_range_preferences": e["hour_range_preferences"],
            "hour_range_caps": e["hour_range_caps"],
        }
        for e in employees
    }
    return {
        "week_start_date": "2026-08-31",
        "num_days": 7,
        "current_location": {"id": "loc1", "name": "Main"},
        "current_employees": employees,
        "current_parsed_shifts": shifts,
        "availability_draft": availability_draft or {},
        "range_counts_before": {},
        "employee_preferences": prefs,
        "errors": [],
        "draft_schedules": [],
        "completed_location_ids": [],
        "current_location_index": 0,
        "failure_entries": [],
    }


MON_TUE_WED = [{"day_of_week": d, "weight": 0.7} for d in (0, 1, 2)]


def test_a_violating_shift_is_annotated_and_counted():
    # Thursday shift, e1 prefers Mon-Wed, and e2 is free and unconstrained.
    shifts = [_shift("e1", "2026-09-03", "09:00", "17:00")]
    out = annotate_preferences(_state(shifts, [_emp("e1", day_prefs=MON_TUE_WED), _emp("e2")]))
    v = out["current_parsed_shifts"][0]["preference_violations"]
    assert v[0]["kind"] == "day" and v[0]["unavoidable"] is False
    assert out["current_preference_summary"] == {
        "shifts_against_preference": 1, "unavoidable": 0, "roster_thin": False,
    }


def test_no_clean_alternative_marks_the_violation_unavoidable():
    # Both employees prefer Mon-Wed; whoever works Thursday violates.
    shifts = [_shift("e1", "2026-09-03", "09:00", "17:00")]
    emps = [_emp("e1", day_prefs=MON_TUE_WED), _emp("e2", day_prefs=MON_TUE_WED)]
    out = annotate_preferences(_state(shifts, emps))
    assert out["current_parsed_shifts"][0]["preference_violations"][0]["unavoidable"] is True
    assert out["current_preference_summary"] == {
        "shifts_against_preference": 1, "unavoidable": 1, "roster_thin": True,
    }


def test_an_alternative_already_booked_does_not_count_as_free():
    shifts = [_shift("e1", "2026-09-03", "09:00", "17:00")]
    emps = [_emp("e1", day_prefs=MON_TUE_WED), _emp("e2")]
    draft = {"e2": [{"start": "2026-09-03T08:00:00-04:00", "end": "2026-09-03T12:00:00-04:00"}]}
    out = annotate_preferences(_state(shifts, emps, availability_draft=draft))
    assert out["current_preference_summary"]["unavoidable"] == 1


def test_an_alternative_without_the_role_does_not_count():
    shifts = [_shift("e1", "2026-09-03", "09:00", "17:00")]
    e2 = _emp("e2")
    e2["roles"] = [{"role_id": "r9", "role_name": "Server", "skill_level": 3}]
    out = annotate_preferences(_state(shifts, [_emp("e1", day_prefs=MON_TUE_WED), e2]))
    assert out["current_preference_summary"]["unavoidable"] == 1


def test_clean_schedules_have_no_summary_noise():
    shifts = [_shift("e1", "2026-08-31", "09:00", "17:00")]  # Monday
    out = annotate_preferences(_state(shifts, [_emp("e1", day_prefs=MON_TUE_WED)]))
    assert out["current_parsed_shifts"][0]["preference_violations"] == []
    assert out["current_preference_summary"] == {
        "shifts_against_preference": 0, "unavoidable": 0, "roster_thin": False,
    }


def test_statuses_and_shift_count_are_untouched():
    shifts = [
        _shift("e1", "2026-09-03", "09:00", "17:00"),
        _shift("VACANT", "2026-09-03", "17:00", "22:00", status="VACANT"),
    ]
    out = annotate_preferences(_state(shifts, [_emp("e1", day_prefs=MON_TUE_WED)]))
    assert [s["status"] for s in out["current_parsed_shifts"]] == ["ok", "VACANT"]
    assert len(out["current_parsed_shifts"]) == 2


def test_a_broken_state_degrades_instead_of_raising():
    state = _state([_shift("e1", "2026-09-03", "09:00", "17:00")], [_emp("e1")])
    state["week_start_date"] = "garbage"
    out = annotate_preferences(state)
    assert len(out["current_parsed_shifts"]) == 1
    assert out["current_preference_summary"] is None


def test_emit_result_carries_the_summary():
    state = _state([_shift("e1", "2026-09-03", "09:00", "17:00")], [_emp("e1", day_prefs=MON_TUE_WED)])
    state.update(annotate_preferences(state))
    out = emit_result(state)
    assert out["draft_schedules"][0]["preference_summary"]["shifts_against_preference"] == 1
