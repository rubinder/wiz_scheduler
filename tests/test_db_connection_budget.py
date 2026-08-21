"""The connection pool must fit inside the database's connection limit.

The pool is per PROCESS, not per task, so the real ceiling is

    UVICORN_WORKERS x (DB_POOL_SIZE + DB_MAX_OVERFLOW) x MAX_ECS_TASKS

At 4 workers x (20 + 40) that was 240 connections from a single task against
a db.t3.micro limit of ~112. It never bit because the pools sat idle at
near-zero traffic — the failure was invisible precisely because there was no
load and no test.

These tests make the arithmetic explicit so it cannot silently regress: a
future change to the pool, the worker count, the task count or the instance
class fails here rather than in production under the first real load.
"""

import pathlib
import re

import pytest

from backend.config import settings


def _per_process() -> int:
    return settings.DB_POOL_SIZE + settings.DB_MAX_OVERFLOW


def _peak_connections() -> int:
    return _per_process() * settings.UVICORN_WORKERS * settings.MAX_ECS_TASKS


def test_peak_demand_fits_the_usable_budget():
    peak = _peak_connections()
    assert peak <= settings.DB_USABLE_CONNECTIONS, (
        f"{settings.UVICORN_WORKERS} workers x {_per_process()} connections x "
        f"{settings.MAX_ECS_TASKS} tasks = {peak}, over the "
        f"{settings.DB_USABLE_CONNECTIONS} the app may claim"
    )


def test_usable_budget_leaves_room_for_operations():
    """The remainder covers superuser_reserved_connections, alembic on task
    start, one-off ECS tasks (seed, backfills) and a human with psql."""
    headroom = settings.DB_MAX_CONNECTIONS - settings.DB_USABLE_CONNECTIONS
    assert headroom >= 15, (
        f"only {headroom} connections left for migrations, one-off tasks and "
        f"operators; raise DB_MAX_CONNECTIONS or lower DB_USABLE_CONNECTIONS"
    )


def test_usable_budget_does_not_exceed_the_instance_limit():
    assert settings.DB_USABLE_CONNECTIONS < settings.DB_MAX_CONNECTIONS


def test_a_single_task_alone_fits():
    """Even before scaling out, one task must not exhaust the database —
    this is the case that was actually broken."""
    one_task = _per_process() * settings.UVICORN_WORKERS
    assert one_task <= settings.DB_USABLE_CONNECTIONS


def test_worker_count_matches_the_dockerfile():
    """UVICORN_WORKERS is only meaningful if it matches what actually runs.

    The Dockerfile CMD is the source of truth for the process count; this
    setting exists so the budget above can be computed. If they drift, the
    budget silently describes a system that does not exist.
    """
    dockerfile = pathlib.Path(__file__).resolve().parents[1] / "Dockerfile"
    text = dockerfile.read_text()

    match = re.search(r"--workers\s+(\d+)", text)
    assert match, "Dockerfile CMD no longer sets --workers"

    assert int(match.group(1)) == settings.UVICORN_WORKERS, (
        f"Dockerfile runs {match.group(1)} uvicorn workers but "
        f"UVICORN_WORKERS is {settings.UVICORN_WORKERS}; the connection "
        f"budget is computed from the setting and would be wrong"
    )


def test_pool_is_large_enough_for_concurrent_generations():
    """POST /schedules/generate holds its session for the whole streamed
    generation, including across the Anthropic call, so a connection is
    pinned for tens of seconds. The pool must absorb several at once per
    worker rather than serialising them behind pool_timeout."""
    assert _per_process() >= 8, (
        "too few connections per process to run concurrent AI generations; "
        "each pins one for the duration of the stream"
    )


@pytest.mark.parametrize(
    "pool,overflow,workers,tasks",
    [
        (20, 40, 4, 1),   # the configuration this fixes: 240 from one task
        (20, 40, 4, 2),   # and 480 once scaled out
        (5, 5, 4, 3),      # 120 — over budget at three tasks
    ],
)
def test_known_bad_configurations_are_rejected(pool, overflow, workers, tasks):
    """Guards the guard: prove the arithmetic actually catches over-commitment
    rather than passing whatever it is given."""
    peak = (pool + overflow) * workers * tasks
    assert peak > settings.DB_USABLE_CONNECTIONS
