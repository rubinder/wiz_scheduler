"""Hard filtering and soft scoring for the three preference parameters.

Score convention matches local_scheduler._affinity_score: LOWER IS BETTER, on
the same +/-50 point scale, so the terms compose without rescaling.

The most important assertion here is test_no_preferences_scores_zero: an
employee with no rows must be completely unaffected, which is what makes the
whole feature additive.
"""

from backend.scheduling.preferences import (
    PREFERENCE_PENALTY,
    blocked_by_hard_preference,
    preference_score,
)

MONDAY, TUESDAY = 0, 1


def _emp(day_prefs=None, range_prefs=None, caps=None):
    return {
        "id": "e1",
        "day_preferences": day_prefs or [],
        "hour_range_preferences": range_prefs or [],
        "hour_range_caps": caps or [],
    }


def test_no_preferences_scores_zero():
    assert preference_score(_emp(), MONDAY, "09:00", "17:00", {}) == 0.0


def test_no_preferences_is_never_blocked():
    assert blocked_by_hard_preference(_emp(), MONDAY, "09:00", "17:00", {}) is False


def test_preferred_day_is_not_penalised():
    emp = _emp(day_prefs=[{"day_of_week": MONDAY, "weight": 0.7}])
    assert preference_score(emp, MONDAY, "09:00", "17:00", {}) == 0.0


def test_non_preferred_day_is_penalised_in_proportion_to_weight():
    emp = _emp(day_prefs=[{"day_of_week": MONDAY, "weight": 0.7}])
    assert preference_score(emp, TUESDAY, "09:00", "17:00", {}) == 0.7 * PREFERENCE_PENALTY


def test_weight_zero_contributes_nothing():
    emp = _emp(day_prefs=[{"day_of_week": MONDAY, "weight": 0.0}])
    assert preference_score(emp, TUESDAY, "09:00", "17:00", {}) == 0.0


def test_hard_day_preference_blocks_other_days():
    emp = _emp(day_prefs=[{"day_of_week": MONDAY, "weight": 1.0}])
    assert blocked_by_hard_preference(emp, TUESDAY, "09:00", "17:00", {}) is True
    assert blocked_by_hard_preference(emp, MONDAY, "09:00", "17:00", {}) is False


def test_soft_day_preference_never_blocks():
    emp = _emp(day_prefs=[{"day_of_week": MONDAY, "weight": 0.9}])
    assert blocked_by_hard_preference(emp, TUESDAY, "09:00", "17:00", {}) is False


def test_hour_range_preference_penalises_a_non_matching_shift():
    emp = _emp(range_prefs=[{"start_time": "13:00", "end_time": "17:00", "weight": 0.5}])
    assert preference_score(emp, MONDAY, "13:00", "17:00", {}) == 0.0
    assert preference_score(emp, MONDAY, "06:00", "10:00", {}) == 0.5 * PREFERENCE_PENALTY


def test_hard_hour_range_preference_blocks_a_non_matching_shift():
    emp = _emp(range_prefs=[{"start_time": "13:00", "end_time": "17:00", "weight": 1.0}])
    assert blocked_by_hard_preference(emp, MONDAY, "06:00", "10:00", {}) is True
    assert blocked_by_hard_preference(emp, MONDAY, "13:00", "17:00", {}) is False


def test_cap_penalises_only_once_the_allowance_is_used():
    cap = {"start_time": "16:00", "end_time": "22:00", "max_per_week": 3, "weight": 0.7}
    emp = _emp(caps=[cap])
    key = ("e1", "16:00", "22:00")
    assert preference_score(emp, MONDAY, "16:00", "22:00", {key: 2}) == 0.0
    assert preference_score(emp, MONDAY, "16:00", "22:00", {key: 3}) == 0.7 * PREFERENCE_PENALTY


def test_hard_cap_blocks_once_the_allowance_is_used():
    cap = {"start_time": "16:00", "end_time": "22:00", "max_per_week": 3, "weight": 1.0}
    emp = _emp(caps=[cap])
    key = ("e1", "16:00", "22:00")
    assert blocked_by_hard_preference(emp, MONDAY, "16:00", "22:00", {key: 3}) is True
    assert blocked_by_hard_preference(emp, MONDAY, "16:00", "22:00", {key: 2}) is False


def test_a_cap_does_not_apply_to_a_shift_outside_its_range():
    cap = {"start_time": "16:00", "end_time": "22:00", "max_per_week": 3, "weight": 1.0}
    emp = _emp(caps=[cap])
    key = ("e1", "16:00", "22:00")
    assert blocked_by_hard_preference(emp, MONDAY, "06:00", "10:00", {key: 9}) is False


def test_penalties_from_several_parameters_add_up():
    emp = _emp(
        day_prefs=[{"day_of_week": MONDAY, "weight": 0.4}],
        range_prefs=[{"start_time": "13:00", "end_time": "17:00", "weight": 0.6}],
    )
    expected = (0.4 + 0.6) * PREFERENCE_PENALTY
    assert preference_score(emp, TUESDAY, "06:00", "10:00", {}) == expected
