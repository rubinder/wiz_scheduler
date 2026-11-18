"""CLI for the host agent evaluation suite (#259).

    python -m backend.reservations.evals.run                 # replay (CI, offline)
    python -m backend.reservations.evals.run --live          # real models
    python -m backend.reservations.evals.run --recognizer noisy --seed 259

Voice scenarios run through the voice bridge in process with a speech
recognizer: `exact` (default) hears every word, `noisy` makes deterministic
phone-line confusions (seeded by --seed, default 259). `lex` (real Polly to
Lex audio) is not available yet.

Replay runs every recording in `recordings/` against the real core, tools and
database with the recorded model responses. Live needs ANTHROPIC_API_KEY,
prints a cost estimate first and refuses above $1.00 without --yes.

Every run happens in a scratch Postgres schema `evals_<utc timestamp>` of
DATABASE_URL, created and dropped here; the public schema is never touched.
The scratch run uses the owner role with no `app_role` / RLS binding (the
isolation is the schema, not row-level security): `settings.RLS_ENFORCE` is
forced off for the duration of `amain` and restored after, because with it
on every session that names a company would `SET LOCAL ROLE app_role`, which
has no grants on the scratch tables. A DATABASE_URL whose host is not local
is refused unless --allow-remote-db is passed.

Each run (meta, results, report) is written to `evals-runs/<run-id>.json` at
the repo root before the scratch schema is dropped (decision 36): there are
no eval tables in any real database.

Exit codes: 0 all passed, 1 a scenario failed, 2 usage / missing key,
3 cost guard refused, 4 remote database refused.
"""

import argparse
import asyncio
import dataclasses
import logging
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.config import settings
from backend.reservations.evals.diner import DINER_MAX_TOKENS, DINER_MODEL
from backend.reservations.evals.voice import DEFAULT_SEED, make_recognizer

logger = logging.getLogger(__name__)

MAX_DINER_TURNS = 20
STALE_AFTER = timedelta(hours=1)
INPUT_TOKEN_ESTIMATE = 6000
INPUT_TOKEN_GROWTH = 800  # per diner turn: the history the next call resends
COST_LIMIT_USD = 1.00
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "host.docker.internal", "postgres", "db"}
_SCHEMA_RE = re.compile(r"^evals_[0-9]{8}t[0-9]{6}z$")


def cost_estimate(scenario_count: int, repeat: int) -> float:
    """scenarios x repeat x 20 diner turns x (MAX_TOOL_ROUNDS agent calls +
    1 diner call). Each call is priced at max output tokens plus an input
    estimate that grows with the conversation: 6000 + 800 x the turn index
    (0-based), at the configured agent prices. The diner is priced at the
    agent's prices too (both are Haiku), with 300 max output. An estimate,
    not a bound: input can run past it, though real runs end well before 20
    turns and rarely use every tool round."""
    inp = settings.RESERVATION_AGENT_INPUT_USD_PER_MTOK
    out = settings.RESERVATION_AGENT_OUTPUT_USD_PER_MTOK
    per_scenario = 0.0
    for turn in range(MAX_DINER_TURNS):
        tokens_in = INPUT_TOKEN_ESTIMATE + INPUT_TOKEN_GROWTH * turn
        agent_call = (settings.RESERVATION_AGENT_MAX_TOKENS * out + tokens_in * inp) / 1e6
        diner_call = (DINER_MAX_TOKENS * out + tokens_in * inp) / 1e6
        per_scenario += settings.RESERVATION_AGENT_MAX_TOOL_ROUNDS * agent_call + diner_call
    return scenario_count * repeat * per_scenario


def host_is_local(database_url: str) -> bool:
    url = make_url(database_url)
    if url.host is None:
        # A unix-socket / libpq default connection, unless a host is given
        # as a query parameter.
        return "host" not in url.query
    return url.host in LOCAL_HOSTS


def scratch_schema_name(now: datetime | None = None) -> str:
    return "evals_" + (now or datetime.now(timezone.utc)).strftime("%Y%m%dt%H%M%Sz")


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m backend.reservations.evals.run",
                                description=__doc__.split("\n\n")[0])
    p.add_argument("--live", action="store_true", help="call the real models")
    p.add_argument("--scenarios", default="", help="comma-separated scenario ids")
    p.add_argument("--repeat", type=int, default=1, help="runs per scenario (live)")
    p.add_argument("--yes", action="store_true", help="accept a cost estimate above $1.00")
    p.add_argument("--record", action="store_true",
                   help="live: write recordings for passing scenarios")
    p.add_argument("--notes", default=None, help="free text stored with the run")
    p.add_argument("--cleanup", action="store_true",
                   help="drop every evals_* schema older than 1 hour, then exit")
    p.add_argument("--allow-remote-db", action="store_true",
                   help="allow a DATABASE_URL whose host is not local")
    p.add_argument("--recognizer", choices=("exact", "noisy", "lex"), default="exact",
                   help="speech recognition for voice scenarios (default exact)")
    p.add_argument("--seed", type=int, default=DEFAULT_SEED,
                   help=f"the noisy recognizer's seed (default {DEFAULT_SEED})")
    return p


def _created_at(schema: str) -> datetime | None:
    m = re.match(r"^evals_(\d{8}t\d{6}z)$", schema)
    if not m:
        return None
    return datetime.strptime(m.group(1), "%Y%m%dt%H%M%Sz").replace(tzinfo=timezone.utc)


async def cleanup_stale(database_url: str, now: datetime | None = None) -> list[str]:
    """Drop every `evals_<timestamp>` schema older than an hour."""
    now = now or datetime.now(timezone.utc)
    admin = create_async_engine(database_url, poolclass=NullPool)
    dropped: list[str] = []
    try:
        async with admin.connect() as conn:
            await conn.execution_options(isolation_level="AUTOCOMMIT")
            names = (await conn.execute(text(
                "select schema_name from information_schema.schemata "
                "where schema_name like 'evals\\_%'"))).scalars().all()
            for name in names:
                made = _created_at(name)
                if made is not None and now - made > STALE_AFTER:
                    await conn.execute(text(f'DROP SCHEMA IF EXISTS "{name}" CASCADE'))
                    dropped.append(name)
    finally:
        await admin.dispose()
    return dropped


async def _scratch_run(database_url: str, schema: str, body: Any) -> Any:
    """Create the schema and its tables, run `body(db_factory)`, drop it.

    search_path is the scratch schema first, then public, so the btree_gist
    operator classes (installed in public) still resolve. Tables are created
    with checkfirst=False: with public on the path, the default existence
    check would see the application's tables there and create nothing, and
    the run would write to public. A check afterwards verifies every table
    really is in the scratch schema."""
    assert _SCHEMA_RE.match(schema), schema
    import backend.models  # noqa: F401  (registers the tables)
    import backend.reservations.evals.models  # noqa: F401
    from backend.database import Base

    admin = create_async_engine(database_url, poolclass=NullPool)
    engine = create_async_engine(
        database_url, poolclass=NullPool,
        connect_args={"server_settings": {"search_path": f"{schema},public"}})
    failed = False
    try:
        async with admin.connect() as conn:
            await conn.execution_options(isolation_level="AUTOCOMMIT")
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS btree_gist"))
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        async with engine.begin() as conn:
            await conn.run_sync(lambda c: Base.metadata.create_all(c, checkfirst=False))
            made = set((await conn.execute(text(
                "select table_name from information_schema.tables "
                "where table_schema = :s"), {"s": schema})).scalars().all())
        missing = set(Base.metadata.tables) - made
        if missing:
            raise RuntimeError(f"scratch schema {schema} is missing tables: "
                               f"{sorted(missing)}; refusing to run")
        factory = async_sessionmaker(engine, class_=AsyncSession,
                                     expire_on_commit=False)
        return await body(factory)
    except BaseException:
        failed = True
        raise
    finally:
        await engine.dispose()
        try:
            async with admin.connect() as conn:
                await conn.execution_options(isolation_level="AUTOCOMMIT")
                await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        except Exception:
            logger.exception("could not drop scratch schema %s; remove it with "
                             "--cleanup", schema)
            if not failed:
                raise
        finally:
            await admin.dispose()


async def amain(argv: list[str] | None = None, *, database_url: str | None = None,
                diner_client: Any = None, llm: Any = None,
                runs_dir: Path | None = None) -> int:
    """`database_url`, `diner_client`, `llm` and `runs_dir` are test seams.
    RLS_ENFORCE is off while it runs (see the module docstring)."""
    saved = settings.RLS_ENFORCE
    settings.RLS_ENFORCE = False
    try:
        return await _amain(argv, database_url=database_url,
                            diner_client=diner_client, llm=llm, runs_dir=runs_dir)
    finally:
        settings.RLS_ENFORCE = saved


async def _amain(argv: list[str] | None, *, database_url: str | None,
                 diner_client: Any, llm: Any, runs_dir: Path | None) -> int:
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
    if args.recognizer == "lex":
        print("--recognizer lex is not available yet", file=sys.stderr)
        return 2
    recognizer = make_recognizer(args.recognizer, args.seed)
    if not host_is_local(url) and not args.allow_remote_db:
        print(f"refusing to run: database host {make_url(url).host!r} is not local "
              "(pass --allow-remote-db to override)", file=sys.stderr)
        return 4

    if args.cleanup:
        dropped = await cleanup_stale(url)
        print(f"dropped {len(dropped)} stale schema(s)" + "".join(f"\n  {n}" for n in dropped))
        return 0
    known = {sc.id for sc in load_scenarios()}
    unknown = sorted(set(ids or []) - known)
    if unknown:
        print(f"unknown scenario id(s): {', '.join(unknown)}; known: "
              f"{', '.join(sorted(known))}", file=sys.stderr)
        return 2
    scenarios = load_scenarios(ids=ids)
    if args.live:
        if not settings.ANTHROPIC_API_KEY:
            print("--live needs ANTHROPIC_API_KEY", file=sys.stderr)
            return 2
        estimate = cost_estimate(len(scenarios), args.repeat)
        print(f"cost estimate: ${estimate:.2f} "
              f"({len(scenarios)} scenarios x {args.repeat} x {MAX_DINER_TURNS} turns)")
        if estimate > COST_LIMIT_USD and not args.yes:
            print(f"refusing: estimate exceeds ${COST_LIMIT_USD:.2f}; pass --yes",
                  file=sys.stderr)
            return 3

    web, voice = store.prompt_hashes()
    meta = store.RunMeta(
        git_sha=store.git_sha(), agent_model=settings.RESERVATION_AGENT_MODEL,
        diner_model=DINER_MODEL, prompt_hash_web=web, prompt_hash_voice=voice,
        mode="live" if args.live else "replay", repeat=args.repeat, notes=args.notes)

    async def body(factory: Any) -> tuple[str, bool, Path]:
        from backend.reservations.agent.core import AgentCore
        from backend.reservations.evals.checker import CheckResult
        from backend.reservations.evals.diner import DinerSim
        from backend.reservations.evals.runner import ScenarioOutcome

        outcomes: list[ScenarioOutcome] = []
        recorded: list[tuple[Any, Any]] = []
        interrupted = False
        try:
            if args.live:
                from backend.reservations.agent.llm import AnthropicLlm

                sim = DinerSim(diner_client)
                for scenario in scenarios:
                    for _ in range(args.repeat):
                        client = replay.RecordingLlm(llm or AnthropicLlm())
                        # run_suite turns a run that raises into a failed outcome.
                        res = await run_suite(
                            factory, [scenario], core=AgentCore(client),
                            diner_factory=lambda s: sim, recognizer=recognizer)
                        outcomes += res.outcomes
                        if args.record:
                            recorded += [(o, client.responses) for o in res.outcomes]
            else:
                by_id = {sc.id: sc for sc in scenarios}
                for name, rec in replay.load_recordings():
                    scenario = by_id.get(rec["scenario_id"])
                    if scenario is None:
                        continue
                    try:
                        core = replay.replay_core(rec, name)
                    except replay.ReplayInvalid as exc:
                        outcomes.append(ScenarioOutcome(
                            scenario_id=scenario.id, conversation_id=None,
                            check=CheckResult(passed=False, failures=[str(exc)]),
                            transcript=[], stop="error"))
                        continue
                    diner = replay.replay_diner(rec)
                    res = await run_suite(factory, [scenario], core=core,
                                          diner_factory=lambda s, d=diner: d,
                                          recognizer=recognizer)
                    for o in res.outcomes:
                        left = replay.unused(name, core, diner)
                        if left:
                            o = dataclasses.replace(o, check=CheckResult(
                                passed=False, failures=[*o.check.failures, *left],
                                metrics=o.check.metrics))
                        outcomes.append(o)
        except (KeyboardInterrupt, asyncio.CancelledError):
            interrupted = True
            print("interrupted: reporting the runs finished so far", file=sys.stderr)
        suite = SuiteResult(outcomes=outcomes)
        async with factory() as db:
            run = await store.store_run(db, suite, meta)
            await db.commit()
            results = await store.load_results(db, run.id)
            report = store.format_report(run, results)
            # Before the scratch schema (and the run with it) is dropped.
            saved_to = store.write_run_file(run, results, meta, report,
                                            runs_dir or store.RUNS_DIR)
        for o, responses in recorded:
            if o.check.passed:
                replay.save_recording(replay.record_transcript(o, responses))
        ok = bool(outcomes) and all(o.check.passed for o in outcomes) and not interrupted
        return report, ok, saved_to

    report, ok, saved_to = await _scratch_run(url, scratch_schema_name(), body)
    print(report)
    print(f"run saved to {saved_to}")
    return 0 if ok else 1


def main() -> None:
    sys.exit(asyncio.run(amain()))


if __name__ == "__main__":
    main()
