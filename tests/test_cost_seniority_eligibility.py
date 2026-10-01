"""eligible_for_slot attaches _cost_score/_seniority_score identically for
both scheduling paths — the same way it already attaches _skill (#134)."""
from backend.scheduling.prompts import eligible_for_slot


def _prepared(eid, **overrides):
    base = {
        "id": eid,
        "_role_names": {"Floor"},
        "_day_windows": {"Monday": [("09:00", "17:00")]},
        "roles": [{"role_name": "Floor", "skill_level": 1}],
        "day_blackouts": [],
        "pay_rate": None,
        "hire_date": None,
        "seniority_rank": None,
    }
    base.update(overrides)
    return base


def test_no_data_configured_scores_are_zero():
    pool = [_prepared("e1"), _prepared("e2")]
    result = eligible_for_slot(pool, "Monday", "Floor", "09:00", "17:00")
    assert {c["_cost_score"] for c in result} == {0.0}
    assert {c["_seniority_score"] for c in result} == {0.0}


def test_cost_score_normalized_across_eligible_pool():
    pool = [_prepared("e1", pay_rate=10.0), _prepared("e2", pay_rate=30.0)]
    result = eligible_for_slot(pool, "Monday", "Floor", "09:00", "17:00")
    scores = {c["id"]: c["_cost_score"] for c in result}
    assert scores["e1"] == 0.0
    assert scores["e2"] == 1.0


def test_cost_score_normalized_within_this_slot_only():
    """A third, ineligible candidate (wrong role) must not affect normalization."""
    pool = [
        _prepared("e1", pay_rate=10.0),
        _prepared("e2", pay_rate=30.0),
        _prepared("e3", pay_rate=20.0, _role_names={"Lead"}),
    ]
    result = eligible_for_slot(pool, "Monday", "Floor", "09:00", "17:00")
    ids = {c["id"] for c in result}
    assert ids == {"e1", "e2"}


def test_seniority_score_derived_from_hire_date():
    from datetime import date
    pool = [
        _prepared("e_junior", hire_date=date(2024, 1, 1)),
        _prepared("e_senior", hire_date=date(2020, 1, 1)),
    ]
    result = eligible_for_slot(pool, "Monday", "Floor", "09:00", "17:00")
    scores = {c["id"]: c["_seniority_score"] for c in result}
    assert scores["e_senior"] == 0.0
    assert scores["e_junior"] == 1.0
