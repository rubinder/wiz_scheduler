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
