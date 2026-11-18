"""CLI for the host agent evaluation suite (#259).

    python -m backend.reservations.evals.run                 # replay (CI, offline)
    python -m backend.reservations.evals.run --live          # real models

Replay runs every recording in `recordings/` against the real core, tools and
database with the recorded model responses. Live needs ANTHROPIC_API_KEY,
prints a cost upper bound first and refuses above $1.00 without --yes.

Every run happens in a scratch Postgres schema `evals_<utc timestamp>` of
DATABASE_URL, created and dropped here; the public schema is never touched.
The scratch run uses the owner role with no `app_role` / RLS binding (the
isolation is the schema, not row-level security). A DATABASE_URL whose host
is not local is refused unless --allow-remote-db is passed.

Exit codes: 0 all passed, 1 a scenario failed, 2 usage / missing key,
3 cost guard refused, 4 remote database refused.
"""

import argparse
import asyncio
import re
import sys
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.config import settings
from backend.reservations.evals.diner import DINER_MAX_TOKENS, DINER_MODEL

MAX_DINER_TURNS = 20
INPUT_TOKEN_ESTIMATE = 6000
COST_LIMIT_USD = 1.00
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "host.docker.internal", "postgres", "db"}
_SCHEMA_RE = re.compile(r"^evals_[0-9]{8}t[0-9]{6}z$")


def cost_upper_bound(scenario_count: int, repeat: int) -> float:
    """scenarios x repeat x 20 diner turns x (MAX_TOOL_ROUNDS agent calls +
    1 diner call), each at its per-call bound: max output tokens plus a
    6000-token input estimate at the configured prices (the diner uses the
    same prices, with 300 max output)."""
    inp = settings.RESERVATION_AGENT_INPUT_USD_PER_MTOK
    out = settings.RESERVATION_AGENT_OUTPUT_USD_PER_MTOK
    agent_call = (settings.RESERVATION_AGENT_MAX_TOKENS * out
                  + INPUT_TOKEN_ESTIMATE * inp) / 1e6
    diner_call = (DINER_MAX_TOKENS * out + INPUT_TOKEN_ESTIMATE * inp) / 1e6
    per_turn = settings.RESERVATION_AGENT_MAX_TOOL_ROUNDS * agent_call + diner_call
    return scenario_count * repeat * MAX_DINER_TURNS * per_turn


def host_is_local(database_url: str) -> bool:
    return (make_url(database_url).host or "localhost") in LOCAL_HOSTS


def scratch_schema_name(now: datetime | None = None) -> str:
    return "evals_" + (now or datetime.now(timezone.utc)).strftime("%Y%m%dt%H%M%Sz")


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m backend.reservations.evals.run",
                                description=__doc__.split("\n\n")[0])
    p.add_argument("--live", action="store_true", help="call the real models")
    p.add_argument("--scenarios", default="", help="comma-separated scenario ids")
    p.add_argument("--repeat", type=int, default=1, help="runs per scenario (live)")
    p.add_argument("--yes", action="store_true", help="accept a cost bound above $1.00")
    p.add_argument("--record", action="store_true",
                   help="live: write recordings for passing scenarios")
    p.add_argument("--notes", default=None, help="free text stored with the run")
    p.add_argument("--allow-remote-db", action="store_true",
                   help="allow a DATABASE_URL whose host is not local")
    return p


async def _scratch_run(database_url: str, schema: str, body: Any) -> Any:
    """Create the schema and its tables, run `body(db_factory)`, drop it."""
    assert _SCHEMA_RE.match(schema), schema
    import backend.models  # noqa: F401  (registers the tables)
    import backend.reservations.evals.models  # noqa: F401
    from backend.database import Base

    admin = create_async_engine(database_url, poolclass=NullPool)
    engine = create_async_engine(
        database_url, poolclass=NullPool,
        connect_args={"server_settings": {"search_path": f"{schema},public"}})
    try:
        async with admin.connect() as conn:
            await conn.execution_options(isolation_level="AUTOCOMMIT")
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS btree_gist"))
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, class_=AsyncSession,
                                     expire_on_commit=False)
        return await body(factory)
    finally:
        await engine.dispose()
        try:
            async with admin.connect() as conn:
                await conn.execution_options(isolation_level="AUTOCOMMIT")
                await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        finally:
            await admin.dispose()


async def amain(argv: list[str] | None = None, *, database_url: str | None = None,
                diner_client: Any = None, llm: Any = None) -> int:
    """`database_url`, `diner_client` and `llm` are test seams."""
    from backend.reservations.evals import replay, store
    from backend.reservations.evals.runner import SuiteResult, run_suite
    from backend.reservations.evals.scenarios_io import load_scenarios

    args = _parser().parse_args(argv)
    ids = [s for s in args.scenarios.split(",") if s] or None
    url = database_url or settings.DATABASE_URL
    if args.repeat < 1:
        print("--repeat must be at least 1", file=sys.stderr)
        return 2
    if args.record and not (args.live and args.repeat == 1):
        print("--record needs --live and --repeat 1", file=sys.stderr)
        return 2
    if not host_is_local(url) and not args.allow_remote_db:
        print(f"refusing to run: database host {make_url(url).host!r} is not local "
              "(pass --allow-remote-db to override)", file=sys.stderr)
        return 4

    scenarios = load_scenarios(ids=ids)
    if args.live:
        if not settings.ANTHROPIC_API_KEY:
            print("--live needs ANTHROPIC_API_KEY", file=sys.stderr)
            return 2
        bound = cost_upper_bound(len(scenarios), args.repeat)
        print(f"cost upper bound: ${bound:.2f} "
              f"({len(scenarios)} scenarios x {args.repeat} x {MAX_DINER_TURNS} turns)")
        if bound > COST_LIMIT_USD and not args.yes:
            print(f"refusing: bound exceeds ${COST_LIMIT_USD:.2f}; pass --yes",
                  file=sys.stderr)
            return 3

    web, voice = store.prompt_hashes()
    meta = store.RunMeta(
        git_sha=store.git_sha(), agent_model=settings.RESERVATION_AGENT_MODEL,
        diner_model=DINER_MODEL, prompt_hash_web=web, prompt_hash_voice=voice,
        mode="live" if args.live else "replay", repeat=args.repeat, notes=args.notes)

    async def body(factory: Any) -> tuple[str, bool]:
        from backend.reservations.agent.core import AgentCore
        from backend.reservations.evals.diner import DinerSim

        outcomes = []
        recorded: list[tuple[Any, Any]] = []
        if args.live:
            from backend.reservations.agent.llm import AnthropicLlm

            sim = DinerSim(diner_client)
            for scenario in scenarios:
                client = replay.RecordingLlm(llm or AnthropicLlm())
                res = await run_suite(factory, [scenario], core=AgentCore(client),
                                      diner_factory=lambda s: sim, repeat=args.repeat)
                outcomes += res.outcomes
                if args.record:
                    recorded += [(o, client.responses) for o in res.outcomes]
        else:
            by_id = {sc.id: sc for sc in scenarios}
            for name, rec in replay.load_recordings():
                scenario = by_id.get(rec["scenario_id"])
                if scenario is None:
                    continue
                res = await run_suite(factory, [scenario],
                                      core=replay.replay_core(rec, name),
                                      diner_factory=lambda s, r=rec: replay.replay_diner(r))
                outcomes += res.outcomes
        suite = SuiteResult(outcomes=outcomes)
        async with factory() as db:
            run = await store.store_run(db, suite, meta)
            await db.commit()
            report = store.format_report(run, await store.load_results(db, run.id))
        for o, responses in recorded:
            if o.check.passed:
                replay.save_recording(replay.record_transcript(o, responses))
        return report, bool(outcomes) and all(o.check.passed for o in outcomes)

    report, ok = await _scratch_run(url, scratch_schema_name(), body)
    print(report)
    return 0 if ok else 1


def main() -> None:
    sys.exit(asyncio.run(amain()))


if __name__ == "__main__":
    main()
