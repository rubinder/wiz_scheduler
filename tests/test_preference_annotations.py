"""The asterisk feature (#99): one evaluator, reported instead of discarded."""

from backend.scheduling.preferences import (
    blocked_by_hard_preference,
    preference_score,
    violations_for_slot,
)


def _emp(eid="e1", day_prefs=None, range_prefs=None, caps=None):
    return {
        "id": eid,
        "day_preferences": day_prefs or [],
        "hour_range_preferences": range_prefs or [],
        "hour_range_caps": caps or [],
    }


def test_violations_for_slot_returns_the_rows_the_other_two_act_on():
    emp = _emp(
        day_prefs=[{"day_of_week": 0, "weight": 0.5}],
        range_prefs=[{"start_time": "16:00", "end_time": "22:00", "weight": 1.0}],
        caps=[{"start_time": "09:00", "end_time": "17:00", "max_per_week": 1, "weight": 0.7}],
    )
    counts = {("e1", "09:00", "17:00"): 1}
    # Tuesday 09:00-17:00: violates the day pref, the range pref, and the cap.
    v = violations_for_slot(emp, 1, "09:00", "17:00", counts)
    assert sorted(float(x["weight"]) for x in v) == [0.5, 0.7, 1.0]
    assert blocked_by_hard_preference(emp, 1, "09:00", "17:00", counts) is True
    # Only the two soft rows score: (0.5 + 0.7) * 50
    assert preference_score(emp, 1, "09:00", "17:00", counts) == 60.0


def test_no_preferences_means_no_violations():
    assert violations_for_slot(_emp(), 3, "09:00", "17:00", {}) == []


from backend.scheduling.preferences import annotate_preference_violations


def _shift(eid, date, start, end, status="ok"):
    return {
        "employee_id": eid, "employee_name": eid, "role_id": "r1",
        "role_name": "Cook", "location_id": "loc1", "date": date,
        "start_time": f"{date}T{start}:00-04:00",
        "end_time": f"{date}T{end}:00-04:00", "status": status,
    }


def _prefs(day_prefs=None, range_prefs=None, caps=None):
    return {"day_preferences": day_prefs or [],
            "hour_range_preferences": range_prefs or [],
            "hour_range_caps": caps or []}


def test_day_violation_reports_the_whole_preferred_set():
    # 2026-09-03 is a Thursday (weekday 3); prefers Mon/Tue/Wed
    shifts = [_shift("e1", "2026-09-03", "09:00", "17:00")]
    prefs = {"e1": _prefs(day_prefs=[
        {"day_of_week": 0, "weight": 0.7},
        {"day_of_week": 1, "weight": 0.7},
        {"day_of_week": 2, "weight": 0.9},
    ])}
    annotate_preference_violations(shifts, prefs)
    v = shifts[0]["preference_violations"]
    assert v == [{"kind": "day", "weight": 0.9, "days": [0, 1, 2], "unavoidable": False}]


def test_hour_range_violation_carries_the_range():
    shifts = [_shift("e1", "2026-08-31", "09:00", "13:00")]
    prefs = {"e1": _prefs(range_prefs=[
        {"start_time": "16:00", "end_time": "22:00", "weight": 0.6}])}
    annotate_preference_violations(shifts, prefs)
    assert shifts[0]["preference_violations"] == [
        {"kind": "hour_range", "weight": 0.6, "start_time": "16:00",
         "end_time": "22:00", "unavoidable": False}
    ]


def test_cap_marks_the_fourth_shift_not_the_first_three():
    shifts = [_shift("e1", f"2026-09-0{d}", "16:00", "22:00") for d in (1, 2, 3, 4)]
    prefs = {"e1": _prefs(caps=[
        {"start_time": "16:00", "end_time": "22:00", "max_per_week": 3, "weight": 0.8}])}
    snapshots = annotate_preference_violations(shifts, prefs)
    assert [s["preference_violations"] for s in shifts[:3]] == [[], [], []]
    assert shifts[3]["preference_violations"] == [
        {"kind": "cap", "weight": 0.8, "start_time": "16:00", "end_time": "22:00",
         "max_per_week": 3, "unavoidable": False}
    ]
    assert snapshots[3] == {("e1", "16:00", "22:00"): 3}
    assert snapshots[0] == {}


def test_cap_count_is_seeded_from_earlier_locations():
    shifts = [_shift("e1", "2026-09-04", "16:00", "22:00")]
    prefs = {"e1": _prefs(caps=[
        {"start_time": "16:00", "end_time": "22:00", "max_per_week": 3, "weight": 0.8}])}
    annotate_preference_violations(shifts, prefs, seed_counts={("e1", "16:00", "22:00"): 3})
    assert shifts[0]["preference_violations"][0]["kind"] == "cap"


def test_shifts_are_walked_in_date_order_regardless_of_input_order():
    later = _shift("e1", "2026-09-04", "16:00", "22:00")
    earlier = [_shift("e1", f"2026-09-0{d}", "16:00", "22:00") for d in (1, 2, 3)]
    shifts = [later] + earlier
    prefs = {"e1": _prefs(caps=[
        {"start_time": "16:00", "end_time": "22:00", "max_per_week": 3, "weight": 0.8}])}
    annotate_preference_violations(shifts, prefs)
    assert shifts[0]["preference_violations"][0]["kind"] == "cap"
    assert all(s["preference_violations"] == [] for s in shifts[1:])


def test_hard_preferences_are_never_reported():
    shifts = [_shift("e1", "2026-09-03", "09:00", "17:00")]
    prefs = {"e1": _prefs(day_prefs=[{"day_of_week": 0, "weight": 1.0}])}
    annotate_preference_violations(shifts, prefs)
    assert shifts[0]["preference_violations"] == []


def test_vacant_conflict_and_unconfigured_shifts_get_an_empty_list():
    shifts = [
        _shift("VACANT", "2026-09-03", "09:00", "17:00", status="VACANT"),
        _shift("e1", "2026-09-03", "09:00", "17:00", status="CONFLICT"),
        _shift("e2", "2026-09-03", "09:00", "17:00"),
    ]
    prefs = {"e1": _prefs(day_prefs=[{"day_of_week": 0, "weight": 0.5}])}
    annotate_preference_violations(shifts, prefs)
    assert [s["preference_violations"] for s in shifts] == [[], [], []]


def test_a_malformed_shift_is_skipped_and_the_rest_annotated():
    bad = {"employee_id": "e1", "status": "ok", "date": "not-a-date",
           "start_time": "??", "end_time": "??"}
    good = _shift("e1", "2026-09-03", "09:00", "17:00")
    prefs = {"e1": _prefs(day_prefs=[{"day_of_week": 0, "weight": 0.5}])}
    annotate_preference_violations([bad, good], prefs)
    assert bad["preference_violations"] == []
    assert good["preference_violations"][0]["kind"] == "day"


def test_empty_preferences_is_a_no_op_that_still_sets_the_field():
    s = _shift("e1", "2026-09-03", "09:00", "17:00")
    annotate_preference_violations([s], {})
    assert s["preference_violations"] == []
