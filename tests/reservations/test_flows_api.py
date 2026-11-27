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


# ── PUT /flows: only flows and editors, everything else as stored ──

FLOWS_URL = f"/api/v1/reservations/locations/{LOCATION_ID}/flows"


async def _put_flows(client, json: dict, headers: dict):
    """PUT /flows from a fresh copy: the flows_version the page just loaded."""
    version = (await client.get(URL, headers=headers)).json()["flows_version"]
    return await client.put(FLOWS_URL, json={"flows_version": version, **json},
                            headers=headers)


async def test_the_flows_route_keeps_rules_a_colleague_changed_meanwhile(
        client, manager_token, seed_location):
    h = _auth(manager_token)
    opened = await _rules(client, manager_token)  # the flow page's copy
    changed = {**opened, "closures": ["2030-01-01"],
               "persona": {**opened["persona"], "greeting": "Hi, automated host here"}}
    assert (await client.put(URL, json=changed, headers=h)).status_code == 200
    r = await _put_flows(client, json={"flows": {"web": _flow(), "voice": None}}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["version"] == 2
    got = await _rules(client, manager_token)
    assert got["flows"]["web"]["start"] == "greet"
    assert got["closures"] == ["2030-01-01"]
    assert got["persona"]["greeting"] == "Hi, automated host here"


async def test_the_flows_route_checks_permissions_and_the_flow(
        client, manager_token, regular_token, seed_location):
    body = {"flows": {"web": _flow()}}
    r = await _put_flows(client, json=body, headers=_auth(regular_token))
    assert r.status_code == 403 and r.json()["detail"]["code"] == "flow_edit_not_allowed"
    r = await _put_flows(client, json={**body, "flow_editor_ids": [REGULAR_MANAGER_ID]},
                         headers=_auth(regular_token))
    assert r.status_code == 403 and r.json()["detail"]["code"] == "admin_only"
    r = await _put_flows(client, json={**body, "flow_editor_ids": [REGULAR_MANAGER_ID]},
                         headers=_auth(manager_token))
    assert r.status_code == 200, r.text
    # The admin omits the list on the next save: it is kept.
    r = await _put_flows(client, json={"flows": {"voice": _flow()}},
                         headers=_auth(manager_token))
    assert r.json()["rules"]["flow_editor_ids"] == [REGULAR_MANAGER_ID]
    r = await _put_flows(client, json=body, headers=_auth(regular_token))
    assert r.status_code == 200, r.text
    r = await _put_flows(client, json={"flows": {"web": _flow(start="nope")}},
                         headers=_auth(regular_token))
    assert r.status_code == 422 and r.json()["detail"]["code"] == "invalid_flow"


# ── flow_editor_ids ──

@pytest.mark.parametrize("url", [URL, FLOWS_URL])
async def test_flow_editors_must_be_regular_managers_of_the_location(
        client, manager_token, regular_token, seed_location, url):
    from tests.conftest import MANAGER_USER_ID

    rules = await _rules(client, manager_token)
    for bad in (["nosuchid"], [MANAGER_USER_ID]):  # unknown; an admin
        body = ({**rules, "flow_editor_ids": bad} if url == URL
                else {"flows": rules["flows"], "flow_editor_ids": bad, "flows_version": 0})
        r = await client.put(url, json=body, headers=_auth(manager_token))
        assert r.status_code == 422, (url, bad)
        assert r.json()["detail"]["code"] == "invalid_flow_editor"


async def test_an_unassigned_editor_is_dropped_on_the_next_save(
        client, manager_token, regular_token, seed_location, db_session):
    from sqlalchemy import delete

    from backend.models import ManagerLocation

    rules = await _rules(client, manager_token)
    await client.put(URL, json={**rules, "flow_editor_ids": [REGULAR_MANAGER_ID]},
                     headers=_auth(manager_token))
    await db_session.execute(delete(ManagerLocation).where(
        ManagerLocation.manager_id == REGULAR_MANAGER_ID))
    await db_session.commit()
    # Still listed, so the editor can show it as stale ...
    rules = await _rules(client, manager_token)
    assert rules["flow_editor_ids"] == [REGULAR_MANAGER_ID]
    # ... and any save drops it rather than refusing.
    r = await client.put(URL, json=rules, headers=_auth(manager_token))
    assert r.status_code == 200, r.text
    assert r.json()["rules"]["flow_editor_ids"] == []


async def test_the_flows_route_drops_an_unassigned_editor_too(
        client, manager_token, regular_token, seed_location, db_session):
    from sqlalchemy import delete

    from backend.models import ManagerLocation

    r = await _put_flows(client, json={"flows": {"web": None, "voice": None},
                                          "flow_editor_ids": [REGULAR_MANAGER_ID]},
                         headers=_auth(manager_token))
    assert r.status_code == 200, r.text
    await db_session.execute(delete(ManagerLocation).where(
        ManagerLocation.manager_id == REGULAR_MANAGER_ID))
    await db_session.commit()
    assert (await _rules(client, manager_token))["flow_editor_ids"] == [REGULAR_MANAGER_ID]
    r = await _put_flows(client, json={"flows": {"web": _flow(), "voice": None}},
                         headers=_auth(manager_token))
    assert r.status_code == 200, r.text
    assert r.json()["rules"]["flow_editor_ids"] == []


# ── stale saves (optimistic concurrency on the flows) ──

async def _flows_version(client, token) -> int:
    return (await client.get(URL, headers=_auth(token))).json()["flows_version"]


async def test_a_save_from_a_stale_copy_of_the_flows_is_409(client, manager_token,
                                                            seed_location):
    h = _auth(manager_token)
    loaded = await _flows_version(client, manager_token)  # both tabs open here
    r = await client.put(FLOWS_URL, json={"flows": {"web": _flow(), "voice": None},
                                          "flows_version": loaded}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["flows_version"] == loaded + 1
    # The second tab still holds the copy it loaded.
    r = await client.put(FLOWS_URL, json={"flows": {"web": None, "voice": _flow()},
                                          "flows_version": loaded}, headers=h)
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "flow_conflict"
    got = await _rules(client, manager_token)
    assert got["flows"]["web"]["start"] == "greet" and got["flows"]["voice"] is None


async def test_a_save_from_the_current_copy_saves_and_bumps_the_version(
        client, manager_token, seed_location):
    h = _auth(manager_token)
    v = await _flows_version(client, manager_token)
    for i in range(2):
        r = await client.put(FLOWS_URL, json={"flows": {"web": _flow() if i else None,
                                                        "voice": _flow()},
                                              "flows_version": v}, headers=h)
        assert r.status_code == 200, r.text
        assert r.json()["flows_version"] == v + 1
        v = r.json()["flows_version"]
    assert await _flows_version(client, manager_token) == v


async def test_a_colleagues_rules_edit_is_not_a_flow_conflict(client, manager_token,
                                                              seed_location):
    """Only a change to the flows (or their editors) makes a copy stale: the
    flow page still saves over a colleague's hours or greeting edit."""
    h = _auth(manager_token)
    opened = await _rules(client, manager_token)
    loaded = await _flows_version(client, manager_token)
    assert (await client.put(URL, json={**opened, "closures": ["2030-01-01"]},
                             headers=h)).status_code == 200
    assert await _flows_version(client, manager_token) == loaded
    r = await client.put(FLOWS_URL, json={"flows": {"web": _flow(), "voice": None},
                                          "flows_version": loaded}, headers=h)
    assert r.status_code == 200, r.text


async def test_a_rules_save_that_changes_the_flows_bumps_their_version(
        client, manager_token, seed_location):
    h = _auth(manager_token)
    rules = await _rules(client, manager_token)
    loaded = await _flows_version(client, manager_token)
    r = await client.put(URL, json={**rules, "flows": {"web": _flow()}}, headers=h)
    assert r.status_code == 200 and r.json()["flows_version"] == loaded + 1
    r = await client.put(FLOWS_URL, json={"flows": {"web": None, "voice": None},
                                          "flows_version": loaded}, headers=h)
    assert r.status_code == 409


async def test_the_flows_version_is_required(client, manager_token, seed_location):
    r = await client.put(FLOWS_URL, json={"flows": {"web": None, "voice": None}},
                         headers=_auth(manager_token))
    assert r.status_code == 422


async def test_two_racing_saves_from_one_copy_save_once(isolated_client, manager_token,
                                                        seed_location):
    import asyncio

    h = _auth(manager_token)
    loaded = (await isolated_client.get(URL, headers=h)).json()["flows_version"]
    a, b = await asyncio.gather(*(
        isolated_client.put(FLOWS_URL, json={"flows": {"web": _flow(), "voice": None},
                                             "flows_version": loaded}, headers=h)
        for _ in range(2)))
    assert sorted((a.status_code, b.status_code)) == [200, 409], (a.text, b.text)
