"""Nightly AI host analytics job (#259 step 10).

For each UTC day: extract the agent data, land it as Parquet in the lake,
run the data-quality checks, and replace that day's agent_daily_metrics
(backend/analytics/run.py).

By default it processes yesterday AND the day before (UTC). A day is read
once as a snapshot, so a conversation still open at midnight lands with its
state of that moment; re-processing the day before the next night gives it
its final state. Each day is idempotent, so the second pass is cheap.

Run manually:
    python -m backend.scripts.run_agent_analytics                 # D-1 and D-2
    python -m backend.scripts.run_agent_analytics --date 2026-11-18
    python -m backend.scripts.run_agent_analytics --backfill 2026-11-01 2026-11-18
    python -m backend.scripts.run_agent_analytics --local /tmp/lake

Without --local it writes to s3://ANALYTICS_BUCKET/ANALYTICS_PREFIX; an
empty ANALYTICS_BUCKET exits 2. eval_results land only where the local
evals-runs/ directory exists (a developer machine), never in ECS.

Exit status: 0 when every day passed, 1 when any day failed a quality
check or raised (the operator is alerted; that day's lake is landed but its
metrics are not written), 2 on a usage or configuration error.
"""
import argparse
import asyncio
import json
import logging
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _date(v: str) -> date:
    try:
        return date.fromisoformat(v)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a YYYY-MM-DD date: {v!r}")


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="run_agent_analytics", description=__doc__.split("\n\n")[0])
    which = p.add_mutually_exclusive_group()
    which.add_argument("--date", type=_date, help="process only this UTC day")
    which.add_argument("--backfill", nargs=2, type=_date, metavar=("START", "END"),
                       help="process every UTC day from START to END inclusive")
    p.add_argument("--local", type=Path, metavar="PATH",
                   help="land under this directory instead of S3")
    return p.parse_args(argv)


def days_to_run(args: argparse.Namespace, today: date) -> list[date]:
    if args.date:
        return [args.date]
    if args.backfill:
        start, end = args.backfill
        return [start + timedelta(days=i) for i in range((end - start).days + 1)]
    return [today - timedelta(days=2), today - timedelta(days=1)]


def make_sink(args: argparse.Namespace):
    from backend.analytics import lake
    from backend.config import settings

    if args.local:
        return lake.LocalSink(args.local)
    if not settings.ANALYTICS_BUCKET:
        return None
    import boto3

    return lake.S3Sink(settings.ANALYTICS_BUCKET, settings.ANALYTICS_PREFIX, boto3.client("s3"))


async def main(argv: list[str] | None = None) -> int:
    from backend.analytics import run as analytics_run
    from backend.database import async_session_factory
    from backend.services.operator_alerts import send_analytics_run_failed_alert

    args = parse_args(argv)
    days = days_to_run(args, datetime.now(timezone.utc).date())
    if args.backfill and not days:
        print("--backfill START must not be after END", file=sys.stderr)
        return 2
    sink = make_sink(args)
    if sink is None:
        print("ANALYTICS_BUCKET is empty: set it or pass --local PATH", file=sys.stderr)
        return 2

    status = 0
    for d in days:
        try:
            async with async_session_factory() as db:
                summary = await analytics_run.run(d, sink, db)
        except Exception as exc:
            # The type only: a database error's message can quote row values.
            logger.error("analytics %s raised %s", d, type(exc).__name__)
            send_analytics_run_failed_alert(d.isoformat(), type(exc).__name__)
            summary = {"day": d.isoformat(), "ok": False, "error": type(exc).__name__}
        print(json.dumps(summary, default=str))
        if not summary["ok"]:
            status = 1
    return status


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
