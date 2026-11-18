"""The eval CLI: guards without network, and replay end to end."""

from typing import Any

import pytest
from sqlalchemy import text

from backend.config import settings
from backend.reservations.evals import run
from tests.conftest import _get_test_engine


class _NoCalls:
    """A client that fails the test if anything calls it."""

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"API touched: {name}")


def _url() -> str:
    return _get_test_engine().url.render_as_string(hide_password=False)


def test_cost_bound_is_above_the_limit_for_the_full_suite() -> None:
    assert run.cost_upper_bound(10, 1) > run.COST_LIMIT_USD
    assert run.cost_upper_bound(1, 1) < run.COST_LIMIT_USD


@pytest.mark.asyncio
async def test_cost_guard_refuses_without_yes(monkeypatch, capsys) -> None:
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "sk-test")
    code = await run.amain(["--live", "--repeat", "3"], database_url=_url(),
                           diner_client=_NoCalls(), llm=_NoCalls())
    assert code == 3
    assert "cost upper bound" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_live_without_key_exits_2(monkeypatch) -> None:
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "")
    code = await run.amain(["--live", "--yes"], database_url=_url(),
                           diner_client=_NoCalls(), llm=_NoCalls())
    assert code == 2


@pytest.mark.asyncio
async def test_remote_database_refused() -> None:
    url = "postgresql+asyncpg://u:p@db.example.com:5432/prod"
    assert await run.amain([], database_url=url) == 4


@pytest.mark.integration
@pytest.mark.asyncio
async def test_replay_runs_end_to_end_and_drops_its_schema(capsys) -> None:
    code = await run.amain(["--allow-remote-db"], database_url=_url())
    out = capsys.readouterr().out
    assert code == 0, out
    assert "pass rate" in out and "book_four_friday_7pm" in out
    async with _get_test_engine().connect() as conn:
        left = (await conn.execute(text(
            "select count(*) from information_schema.schemata "
            "where schema_name like 'evals\\_%'"))).scalar_one()
    assert left == 0
