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
    code = await run.amain([], database_url=_url())
    out = capsys.readouterr().out
    assert code == 0, out
    assert "pass rate" in out and "book_four_friday_7pm" in out
    async with _get_test_engine().connect() as conn:
        left = (await conn.execute(text(
            "select count(*) from information_schema.schemata "
            "where schema_name like 'evals\\_%'"))).scalar_one()
    assert left == 0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_replay_never_writes_to_public(capsys) -> None:
    """The conftest database has the public tables; the run must not touch
    them (search_path includes public for the btree_gist operator classes)."""
    async def counts() -> tuple[int, int]:
        async with _get_test_engine().connect() as conn:
            out = []
            for tbl in ("eval_runs", "agent_conversations"):
                out.append((await conn.execute(text(f"select count(*) from {tbl}"))).scalar_one())
            return out[0], out[1]

    before = await counts()
    assert await run.amain([], database_url=_url()) == 0, capsys.readouterr().out
    assert await counts() == before


@pytest.mark.asyncio
async def test_unknown_scenario_exits_2() -> None:
    assert await run.amain(["--scenarios", "nope"], database_url=_url()) == 2


def test_host_none_is_local_only_without_host_param() -> None:
    assert run.host_is_local("postgresql+asyncpg:///db")
    assert not run.host_is_local("postgresql+asyncpg:///db?host=db.example.com")


@pytest.mark.asyncio
async def test_cleanup_drops_only_stale_schemas() -> None:
    from datetime import datetime, timedelta, timezone

    old = run.scratch_schema_name(datetime.now(timezone.utc) - timedelta(hours=3))
    new = run.scratch_schema_name()
    async with _get_test_engine().begin() as conn:
        for n in (old, new):
            await conn.execute(text(f'create schema "{n}"'))
    try:
        assert await run.cleanup_stale(_url()) == [old]
    finally:
        async with _get_test_engine().begin() as conn:
            await conn.execute(text(f'drop schema if exists "{new}" cascade'))
