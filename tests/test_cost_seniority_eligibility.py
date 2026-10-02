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


def test_no_data_configured_scores_are_neutral():
    pool = [_prepared("e1"), _prepared("e2")]
    result = eligible_for_slot(pool, "Monday", "Floor", "09:00", "17:00")
    assert {c["_cost_score"] for c in result} == {0.5}
    assert {c["_seniority_score"] for c in result} == {0.5}


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


def test_seniority_score_manual_rank_takes_precedence_over_hire_date():
    """Regression test for #134: manual seniority_rank should not tie with
    derived ranks when both are present in the pool. The manual-ranked employee
    should have a lower (more senior) score."""
    from datetime import date
    pool = [
        _prepared("e_manual_rank_1", seniority_rank=1, hire_date=date(2024, 1, 1)),
        _prepared("e_derived_rank", hire_date=date(2020, 1, 1)),
    ]
    result = eligible_for_slot(pool, "Monday", "Floor", "09:00", "17:00")
    scores = {c["id"]: c["_seniority_score"] for c in result}
    # e_manual_rank_1 has rank 1 (manual), normalizes to lower score (more senior)
    # e_derived_rank has rank 2 (derived after manual), normalizes to higher score (less senior)
    assert scores["e_manual_rank_1"] < scores["e_derived_rank"], (
        f"Manual rank should win: {scores['e_manual_rank_1']} should be < {scores['e_derived_rank']}"
    )
