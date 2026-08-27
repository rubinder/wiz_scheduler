"""API surface for the three preference types.

The gating assertions are the point: these endpoints must NOT call
assert_paid_plan. The deterministic scheduler is the free tier's product and
these preferences improve it, so gating them would weaken the tier they most
help. A future refactor that adds a paid gate should fail here.
"""

import pytest
from httpx import AsyncClient

BASE = "/api/v1/scheduling-preferences"


@pytest.mark.asyncio
async def test_requires_authentication(client: AsyncClient):
    resp = await client.get(f"{BASE}/days")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_free_plan_is_not_blocked(client: AsyncClient, manager_token: str):
    """Explicitly NOT 402 — these are ungated."""
    resp = await client.get(
        f"{BASE}/days", headers={"Authorization": f"Bearer {manager_token}"}
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_create_defaults_weight_to_seven_tenths(
    client: AsyncClient, manager_token: str, seeded_employee_id: str
):
    resp = await client.post(
        f"{BASE}/days",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"employee_id": seeded_employee_id, "day_of_week": 0},
    )
    assert resp.status_code == 201
    assert resp.json()["weight"] == 0.7


@pytest.mark.asyncio
async def test_weight_above_one_is_rejected(
    client: AsyncClient, manager_token: str, seeded_employee_id: str
):
    resp = await client.post(
        f"{BASE}/days",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"employee_id": seeded_employee_id, "day_of_week": 1, "weight": 1.5},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_two_decimal_weight_is_rejected(
    client: AsyncClient, manager_token: str, seeded_employee_id: str
):
    resp = await client.post(
        f"{BASE}/days",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"employee_id": seeded_employee_id, "day_of_week": 2, "weight": 0.75},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_cannot_create_for_another_companys_employee(
    client: AsyncClient, manager_token: str, other_company_employee_id: str
):
    resp = await client.post(
        f"{BASE}/days",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={"employee_id": other_company_employee_id, "day_of_week": 0},
    )
    assert resp.status_code in (403, 404)


# ── Carried fix A: zero-length hour ranges must be rejected (422) ──
#
# overlap_fraction normalises start==end by adding 24h (the same rule that
# makes overnight ranges like 22:00-06:00 work), so a zero-length range
# would otherwise match every shift at 1.0 — a weight-1.0 row would then
# hard-block an employee from every shift, and a cap on it would match
# every shift it's ever offered.


@pytest.mark.asyncio
async def test_zero_length_hour_range_preference_is_rejected(
    client: AsyncClient, manager_token: str, seeded_employee_id: str
):
    resp = await client.post(
        f"{BASE}/hour-ranges",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={
            "employee_id": seeded_employee_id,
            "start_time": "13:00",
            "end_time": "13:00",
        },
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_zero_length_hour_range_cap_is_rejected(
    client: AsyncClient, manager_token: str, seeded_employee_id: str
):
    resp = await client.post(
        f"{BASE}/caps",
        headers={"Authorization": f"Bearer {manager_token}"},
        json={
            "employee_id": seeded_employee_id,
            "start_time": "13:00",
            "end_time": "13:00",
            "max_per_week": 3,
        },
    )
    assert resp.status_code == 422
