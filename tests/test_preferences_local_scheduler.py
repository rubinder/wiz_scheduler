"""Preferences change the deterministic scheduler only when configured.

test_zero_preferences_is_a_no_op is the single most important test in this
feature. _pick_employee is on the path of all four strategies, so if adding a
scoring term perturbs the no-preference case, every existing schedule changes
silently.
"""

import pytest

from backend.scheduling.preferences import blocked_by_hard_preference
from backend.scheduling.prompts import eligible_for_slot


def _emp(eid, day_prefs=None, range_prefs=None, caps=None):
    return {
        "id": eid,
        "_role_names": {"Cook"},
        "_day_windows": {"Monday": [("00:00", "23:59")]},
        "roles": [{"role_name": "Cook", "skill_level": 3}],
        "day_blackouts": [],
        "day_preferences": day_prefs or [],
        "hour_range_preferences": range_prefs or [],
        "hour_range_caps": caps or [],
    }


def test_zero_preferences_is_a_no_op():
    """With no preferences, the eligible set is exactly what it was before."""
    prepared = [_emp("e1"), _emp("e2")]
    without = eligible_for_slot(prepared, "Monday", "Cook", "09:00", "17:00")
    with_args = eligible_for_slot(
        prepared, "Monday", "Cook", "09:00", "17:00", day_index=0, range_counts={}
    )
    assert [e["id"] for e in without] == ["e1", "e2"]
    assert [e["id"] for e in with_args] == ["e1", "e2"]


def test_hard_day_preference_removes_the_candidate():
    prepared = [
        _emp("e1", day_prefs=[{"day_of_week": 0, "weight": 1.0}]),  # Monday only
        _emp("e2"),
    ]
    # Monday is day_index 0 -> e1 stays
    monday = eligible_for_slot(
        prepared, "Monday", "Cook", "09:00", "17:00", day_index=0, range_counts={}
    )
    assert [e["id"] for e in monday] == ["e1", "e2"]
    # Tuesday -> e1 is filtered out entirely
    prepared[0]["_day_windows"]["Tuesday"] = [("00:00", "23:59")]
    prepared[1]["_day_windows"]["Tuesday"] = [("00:00", "23:59")]
    tuesday = eligible_for_slot(
        prepared, "Tuesday", "Cook", "09:00", "17:00", day_index=1, range_counts={}
    )
    assert [e["id"] for e in tuesday] == ["e2"]


def test_a_slot_can_lose_every_candidate():
    """This is what produces a VACANT shift, and it must not raise."""
    prepared = [_emp("e1", day_prefs=[{"day_of_week": 0, "weight": 1.0}])]
    prepared[0]["_day_windows"]["Tuesday"] = [("00:00", "23:59")]
    assert eligible_for_slot(
        prepared, "Tuesday", "Cook", "09:00", "17:00", day_index=1, range_counts={}
    ) == []


def test_soft_preference_does_not_remove_the_candidate():
    prepared = [_emp("e1", day_prefs=[{"day_of_week": 0, "weight": 0.9}])]
    prepared[0]["_day_windows"]["Tuesday"] = [("00:00", "23:59")]
    out = eligible_for_slot(
        prepared, "Tuesday", "Cook", "09:00", "17:00", day_index=1, range_counts={}
    )
    assert [e["id"] for e in out] == ["e1"]
