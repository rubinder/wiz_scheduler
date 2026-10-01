"""Pure scoring functions for cost, seniority, and overtime (#134).

Lower score = more preferred, matching preferences.preference_score and
local_scheduler._affinity_score. 0.0 is always the "no data configured"
result, which is what keeps this feature additive.
"""
from backend.scheduling.cost_seniority import (
    DEFAULT_OVERTIME_THRESHOLD_HOURS,
    DEFAULT_OVERTIME_PREMIUM_MULTIPLIER,
    cost_score,
    overtime_score,
    resolve_overtime_multiplier,
    resolve_overtime_threshold,
    resolve_seniority_ranks,
    seniority_score,
)


def test_cost_score_none_when_pay_rate_unset():
    assert cost_score({"id": "e1", "pay_rate": None}, [{"id": "e1", "pay_rate": None}]) == 0.0


def test_cost_score_half_when_only_one_rated_candidate():
    pool = [{"id": "e1", "pay_rate": 20.0}]
    assert cost_score(pool[0], pool) == 0.5


def test_cost_score_half_when_all_rates_equal():
    pool = [{"id": "e1", "pay_rate": 20.0}, {"id": "e2", "pay_rate": 20.0}]
    assert cost_score(pool[0], pool) == 0.5
    assert cost_score(pool[1], pool) == 0.5


def test_cost_score_min_max_normalized():
    pool = [{"id": "e1", "pay_rate": 10.0}, {"id": "e2", "pay_rate": 30.0}]
    assert cost_score(pool[0], pool) == 0.0
    assert cost_score(pool[1], pool) == 1.0


def test_cost_score_unrated_employee_excluded_from_range():
    pool = [
        {"id": "e1", "pay_rate": 10.0},
        {"id": "e2", "pay_rate": 30.0},
        {"id": "e3", "pay_rate": None},
    ]
    assert cost_score(pool[2], pool) == 0.0
    assert cost_score(pool[0], pool) == 0.0
    assert cost_score(pool[1], pool) == 1.0


def test_resolve_seniority_ranks_manual_wins_over_hire_date():
    from datetime import date
    pool = [
        {"id": "e1", "seniority_rank": None, "hire_date": date(2020, 1, 1)},
        {"id": "e2", "seniority_rank": 1, "hire_date": date(2024, 1, 1)},
    ]
    ranks = resolve_seniority_ranks(pool)
    assert ranks["e2"] == 1.0
    assert ranks["e1"] == 2.0  # derived ranks start after the highest manual rank (1), so e1 -> 2


def test_resolve_seniority_ranks_hire_date_order():
    from datetime import date
    pool = [
        {"id": "e1", "seniority_rank": None, "hire_date": date(2022, 1, 1)},
        {"id": "e2", "seniority_rank": None, "hire_date": date(2020, 1, 1)},
    ]
    ranks = resolve_seniority_ranks(pool)
    assert ranks["e2"] == 1.0
    assert ranks["e1"] == 2.0


def test_resolve_seniority_ranks_excludes_employees_with_neither_field():
    pool = [{"id": "e1", "seniority_rank": None, "hire_date": None}]
    assert resolve_seniority_ranks(pool) == {}


def test_seniority_score_zero_when_no_resolved_rank():
    assert seniority_score({"id": "e1"}, {}) == 0.0


def test_seniority_score_min_max_normalized():
    ranks = {"e1": 1.0, "e2": 3.0}
    assert seniority_score({"id": "e1"}, ranks) == 0.0
    assert seniority_score({"id": "e2"}, ranks) == 1.0


def test_overtime_score_zero_under_threshold():
    assert overtime_score(hours_committed=30.0, shift_duration_hrs=8.0, threshold=40.0) == 0.0


def test_overtime_score_zero_exactly_at_threshold():
    assert overtime_score(hours_committed=32.0, shift_duration_hrs=8.0, threshold=40.0) == 0.0


def test_overtime_score_positive_and_scaled_over_threshold():
    low = overtime_score(hours_committed=36.0, shift_duration_hrs=8.0, threshold=40.0)
    high = overtime_score(hours_committed=40.0, shift_duration_hrs=8.0, threshold=40.0)
    assert low > 0.0
    assert high > low


def test_resolve_overtime_threshold_location_beats_company_beats_default():
    assert resolve_overtime_threshold(None, None) == DEFAULT_OVERTIME_THRESHOLD_HOURS
    assert resolve_overtime_threshold(35.0, None) == 35.0
    assert resolve_overtime_threshold(35.0, 30.0) == 30.0


def test_resolve_overtime_multiplier_location_beats_company_beats_default():
    assert resolve_overtime_multiplier(None, None) == DEFAULT_OVERTIME_PREMIUM_MULTIPLIER
    assert resolve_overtime_multiplier(2.0, None) == 2.0
    assert resolve_overtime_multiplier(2.0, 1.8) == 1.8
