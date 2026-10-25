"""/metrics is off unless a token is configured, and never echoes request paths."""
import secrets

import pytest
from httpx import AsyncClient

from backend.config import settings

pytestmark = pytest.mark.asyncio

TOKEN = "test-metrics-token"


async def _scrape(client: AsyncClient) -> str:
    resp = await client.get("/metrics", headers={"Authorization": f"Bearer {TOKEN}"})
    assert resp.status_code == 200
    return resp.text


async def test_metrics_is_404_when_no_token_is_configured(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "METRICS_TOKEN", "")
    assert (await client.get("/metrics")).status_code == 404
    # An empty bearer must not match an empty configured token.
    resp = await client.get("/metrics", headers={"Authorization": "Bearer "})
    assert resp.status_code == 404


@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Bearer wrong"}, {"Authorization": TOKEN}],
)
async def test_metrics_is_404_without_the_right_bearer(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, headers: dict[str, str]
) -> None:
    monkeypatch.setattr(settings, "METRICS_TOKEN", TOKEN)
    assert (await client.get("/metrics", headers=headers)).status_code == 404


async def test_metrics_serves_with_the_right_bearer(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "METRICS_TOKEN", TOKEN)
    resp = await client.get("/metrics", headers={"Authorization": f"Bearer {TOKEN}"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")


async def test_path_parameters_are_labelled_by_route_template(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An invite token is the whole credential for accepting the invite, so it
    must never surface in a metric label."""
    monkeypatch.setattr(settings, "METRICS_TOKEN", TOKEN)
    invite_token = secrets.token_urlsafe(32)
    await client.get(f"/api/v1/invites/{invite_token}")
    await client.post(f"/api/v1/invites/{invite_token}/accept", json={})

    body = await _scrape(client)
    assert invite_token not in body
    assert 'endpoint="/api/v1/invites/{token}"' in body
    assert 'endpoint="/api/v1/invites/{token}/accept"' in body


async def test_unmatched_paths_share_one_label(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "METRICS_TOKEN", TOKEN)
    probe = f"/no-such-route-{secrets.token_hex(8)}"
    assert (await client.get(probe)).status_code == 404

    body = await _scrape(client)
    assert probe not in body
    assert 'endpoint="<unmatched>"' in body
