"""Custom conversation flows through the rules API (decision 102): round
trip, `invalid_flow`, and who may edit."""

import pytest

from backend.config import settings
from tests.conftest import LOCATION_ID, REGULAR_MANAGER_ID
from tests.reservations.test_flows import _flow

URL = f"/api/v1/reservations/locations/{LOCATION_ID}/rules"

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.fixture(autouse=True)
def _enabled(monkeypatch):
    monkeypatch.setattr(settings, "RESERVATIONS_ENABLED", True)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _rules(client, token) -> dict:
    return (await client.get(URL, headers=_auth(token))).json()["rules"]


async def test_flows_default_to_null_and_round_trip(client, manager_token, seed_location):
    rules = await _rules(client, manager_token)
    assert rules["flows"] == {"web": None, "voice": None}
    assert rules["flow_editor_ids"] == []
    rules["flows"] = {"web": None, "voice": _flow()}
    r = await client.put(URL, json=rules, headers=_auth(manager_token))
    assert r.status_code == 200, r.text
    got = await _rules(client, manager_token)
    assert got["flows"]["voice"]["start"] == "greet"
    assert [s["id"] for s in got["flows"]["voice"]["steps"]] == ["greet", "when", "book", "staff"]
    assert got["flows"]["web"] is None


async def test_a_save_that_omits_flows_keeps_them(client, manager_token, seed_location):
    rules = await _rules(client, manager_token)
    await client.put(URL, json={**rules, "flows": {"web": _flow()}},
                     headers=_auth(manager_token))
    rest = {k: v for k, v in rules.items() if k not in ("flows", "flow_editor_ids")}
    r = await client.put(URL, json=rest, headers=_auth(manager_token))
    assert r.status_code == 200, r.text
    assert (await _rules(client, manager_token))["flows"]["web"]["start"] == "greet"


async def test_an_invalid_flow_is_422_with_every_problem(client, manager_token, seed_location):
    rules = await _rules(client, manager_token)
    bad = _flow(start="nope")
    bad["steps"][1]["next"] = "ghost"
    r = await client.put(URL, json={**rules, "flows": {"voice": bad}},
                         headers=_auth(manager_token))
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["code"] == "invalid_flow"
    assert {(p["channel"], p["code"]) for p in detail["problems"]} >= {
        ("voice", "missing_start"), ("voice", "unknown_target")}


async def test_a_regular_manager_not_allowed_cannot_edit_flows(
        client, regular_token, seed_location):
    rules = await _rules(client, regular_token)
    r = await client.put(URL, json={**rules, "flows": {"web": _flow()}},
                         headers=_auth(regular_token))
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "flow_edit_not_allowed"
    # Saving other rules with the flows unchanged is fine.
    r = await client.put(URL, json={**rules, "slot_interval_minutes": 30},
                         headers=_auth(regular_token))
    assert r.status_code == 200, r.text


async def test_an_allowed_regular_manager_can_edit_flows(
        client, manager_token, regular_token, seed_location):
    rules = await _rules(client, manager_token)
    r = await client.put(URL, json={**rules, "flow_editor_ids": [REGULAR_MANAGER_ID]},
                         headers=_auth(manager_token))
    assert r.status_code == 200, r.text
    rules = await _rules(client, regular_token)
    r = await client.put(URL, json={**rules, "flows": {"web": _flow()}},
                         headers=_auth(regular_token))
    assert r.status_code == 200, r.text
    assert (await _rules(client, manager_token))["flows"]["web"]["start"] == "greet"


async def test_a_regular_manager_cannot_change_flow_editors(
        client, manager_token, regular_token, seed_location):
    rules = await _rules(client, regular_token)
    r = await client.put(URL, json={**rules, "flow_editor_ids": [REGULAR_MANAGER_ID]},
                         headers=_auth(regular_token))
    assert r.status_code == 403 and r.json()["detail"]["code"] == "admin_only"
    # Omitting the list keeps the admin's choice.
    await client.put(URL, json={**rules, "flow_editor_ids": [REGULAR_MANAGER_ID]},
                     headers=_auth(manager_token))
    mine = {k: v for k, v in rules.items() if k != "flow_editor_ids"}
    r = await client.put(URL, json=mine, headers=_auth(regular_token))
    assert r.status_code == 200, r.text
    assert (await _rules(client, manager_token))["flow_editor_ids"] == [REGULAR_MANAGER_ID]


async def test_the_web_chat_greeting_opens_with_the_flows_first_question(
        client, manager_token, seed_location):
    from backend.reservations.agent import gate
    from tests.conftest import get_test_session_factory

    rules = await _rules(client, manager_token)
    await client.put(URL, json={**rules, "flows": {"web": _flow()}},
                     headers=_auth(manager_token))
    async with get_test_session_factory()() as db:
        web = await gate.greeting(db, seed_location.company_id, seed_location, "web")
        voice = await gate.greeting(db, seed_location.company_id, seed_location, "voice")
    assert web.endswith("Table, or catering?")
    assert voice.endswith("event or private party?")
