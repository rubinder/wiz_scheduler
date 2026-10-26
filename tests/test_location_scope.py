"""LocationScope and the admin / scope dependencies (#149, #150)."""
import pytest
from fastapi import HTTPException
from httpx import AsyncClient

from backend.services.location_scope import UNRESTRICTED, LocationScope
from tests.conftest import LOCATION_ID, OTHER_LOCATION_ID

pytestmark = pytest.mark.asyncio


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_unrestricted_scope_allows_everything() -> None:
    assert not UNRESTRICTED.restricted
    assert UNRESTRICTED.allows("anything")
    assert UNRESTRICTED.allows_any([])
    assert UNRESTRICTED.allows_all([])


async def test_restricted_scope_see_any_edit_all() -> None:
    scope = LocationScope(ids=frozenset({"a", "b"}))
    assert scope.restricted
    assert scope.allows("a") and not scope.allows("c") and not scope.allows(None)
    assert scope.allows_any(["c", "a"]) and not scope.allows_any(["c"])
    assert scope.allows_all(["a", "b"]) and not scope.allows_all(["a", "c"])
    # An employee with no locations is visible and editable to admins only.
    assert not scope.allows_any([]) and not scope.allows_all([])
    assert not scope.allows_any(None) and not scope.allows_all(None)


async def test_require_answers_404() -> None:
    scope = LocationScope(ids=frozenset({"a"}))
    scope.require("a")
    with pytest.raises(HTTPException) as exc:
        scope.require("b", "Schedule")
    assert exc.value.status_code == 404 and exc.value.detail == "Schedule not found"


@pytest.mark.integration
async def test_auth_me_reports_the_scope(
    client: AsyncClient, manager_token: str, regular_token: str
) -> None:
    admin = await client.get("/api/v1/auth/me", headers=_h(manager_token))
    assert admin.status_code == 200
    assert admin.json()["accessible_location_ids"] is None

    regular = await client.get("/api/v1/auth/me", headers=_h(regular_token))
    assert regular.status_code == 200
    body = regular.json()
    assert body["manager_type"] == "regular"
    assert body["accessible_location_ids"] == [LOCATION_ID]
    assert OTHER_LOCATION_ID not in body["accessible_location_ids"]
