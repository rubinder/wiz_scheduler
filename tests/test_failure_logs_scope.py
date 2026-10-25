"""A manager sees only their own company's failure logs."""
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import FailureLog
from tests.conftest import COMPANY2_ID, COMPANY_ID

pytestmark = pytest.mark.asyncio


async def test_list_returns_only_the_callers_company(
    client: AsyncClient,
    db_session: AsyncSession,
    manager_token: str,
    other_company_employee_id: str,  # creates COMPANY2_ID
) -> None:
    for company_id, message in (
        (COMPANY_ID, "own"),
        (COMPANY2_ID, "other tenant"),
        (None, "no company"),
    ):
        db_session.add(
            FailureLog(
                company_id=company_id,
                category="unhandled_exception",
                source="GET /x",
                message=message,
            )
        )
    await db_session.commit()

    resp = await client.get(
        "/api/v1/failure-logs", headers={"Authorization": f"Bearer {manager_token}"}
    )
    assert resp.status_code == 200
    assert [row["message"] for row in resp.json()] == ["own"]
