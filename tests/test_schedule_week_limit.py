"""A generation covers at most one calendar week.

Two layers, tested separately because they fail differently and either could
be removed without the other noticing:

  1. GenerateRequest.num_days carries Field(ge=1, le=7), so the API rejects a
     wider window with a 422 before anything runs.
  2. _validate_num_days in the pipeline catches an internal caller that
     bypasses the schema.

Layer 2 matters because a wider window does not error on its own — it
silently corrupts. The per-day template fusion keys the fused weekly_schedule
by day NAME, so an 8-day window contains two Mondays and the later date's
override quietly overwrites the earlier one.

It also bounds the free plan: FREE_PLAN_SCHEDULES_PER_LOCATION counts
generations, not days, so without a day cap two generations could cover a year.
"""

import pytest
from pydantic import ValidationError

from backend.scheduling.graph import MAX_SCHEDULE_DAYS, _validate_num_days
from backend.schemas.schedule import GenerateRequest

pytestmark = pytest.mark.asyncio


# --- Layer 1: the API schema -------------------------------------------------

async def test_schema_accepts_a_full_week():
    req = GenerateRequest(week_start_date="2026-08-24", num_days=7)
    assert req.num_days == 7


async def test_schema_defaults_to_a_week():
    req = GenerateRequest(week_start_date="2026-08-24")
    assert req.num_days == 7


@pytest.mark.parametrize("bad", [8, 14, 30, 365])
async def test_schema_refuses_more_than_a_week(bad: int):
    with pytest.raises(ValidationError) as exc:
        GenerateRequest(week_start_date="2026-08-24", num_days=bad)
    assert "num_days" in str(exc.value)


@pytest.mark.parametrize("bad", [0, -1])
async def test_schema_refuses_a_non_positive_window(bad: int):
    with pytest.raises(ValidationError):
        GenerateRequest(week_start_date="2026-08-24", num_days=bad)


# --- Layer 2: the pipeline guard --------------------------------------------

async def test_guard_matches_the_schema_bound():
    """If these drift apart, one layer starts accepting what the other
    rejects, and the silent-overwrite bug returns through the gap."""
    field = GenerateRequest.model_fields["num_days"]
    bounds = [m for m in field.metadata if hasattr(m, "le")]
    assert bounds, "num_days lost its upper bound"
    assert bounds[0].le == MAX_SCHEDULE_DAYS


@pytest.mark.parametrize("ok", [1, 3, 7])
async def test_guard_accepts_up_to_a_week(ok: int):
    _validate_num_days(ok)


@pytest.mark.parametrize("bad", [0, -1, 8, 30, 365])
async def test_guard_refuses_outside_a_week(bad: int):
    with pytest.raises(ValueError, match="one calendar week"):
        _validate_num_days(bad)


async def test_guard_runs_before_any_database_work():
    """_load_initial_state must reject the window before querying, so a bad
    call cannot half-build a state. Passing db=None proves nothing touched it."""
    from backend.scheduling.graph import _load_initial_state

    with pytest.raises(ValueError, match="one calendar week"):
        await _load_initial_state("comp0001", "2026-08-24", None, num_days=30)
