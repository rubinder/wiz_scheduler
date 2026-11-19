"""Extract one UTC day of AI host data for the lake (#259 step 10).

Each table is one read-only SQL query over every company, run as the table
owner (a session with no RLS binding, as `cross_tenant_session`), and every
row comes back already redacted:

- conversations whose started_at falls in [day 00:00Z, day+1 00:00Z), so a
  conversation spanning midnight lands on its start day;
- events whose created_at falls in that window (they land on their own
  day, whatever day their conversation started);
- reservations created that day, WizScheduler's and the mock external
  provider's, with a `provider` column;
- agent_usage and screened_calls created that day;
- eval_results from `evals-runs/*.json` whose run date is the day, only
  where that directory exists. It is a local, developer-side record of
  eval runs and is not in the ECS image, so there the table is skipped
  (absent from the result; see `skipped`).

Names, emails, notes and caller numbers are dropped; diner phones become
`diner_key = source_key_for(phone)`; text goes through redact.text and tool
payloads through redact.payload.
"""

import json
import logging
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.analytics import redact
from backend.analytics.schemas import TABLES
from backend.database import cross_tenant_session
from backend.services.rate_limit import source_key_for

logger = logging.getLogger(__name__)

# Where the eval runner writes its run files (reservations/evals/store.py,
# RUNS_DIR). Not imported from there: product code never imports the evals
# package (tests/test_architecture.py, product-imports-evals).
RUNS_DIR = Path(__file__).resolve().parents[2] / "evals-runs"

# The context line core.py puts before the diner's words in a user_turn.
_CONTEXT_PREFIX = "[Now:"
_LABEL_MAX = 64

_CONVERSATIONS = text("""
    SELECT c.id AS conversation_id, c.company_id, c.location_id, c.channel,
           c.provider, c.status, c.outcome, c.reservation_id, c.source_key,
           c.turn_count, c.failed_lookups, c.cost_usd, u.billed_usd,
           c.started_at, c.ended_at
      FROM agent_conversations c
      LEFT JOIN agent_usage u ON u.conversation_id = c.id
     WHERE c.started_at >= :start AND c.started_at < :end
     ORDER BY c.started_at, c.id
""")

_EVENTS = text("""
    SELECT e.id AS event_id, e.conversation_id, e.company_id, c.location_id,
           e.seq, e.type, e.payload, e.latency_ms, e.input_tokens,
           e.output_tokens, e.cache_read_tokens, e.cache_write_tokens,
           e.cost_usd, e.created_at
      FROM agent_events e
      JOIN agent_conversations c ON c.id = e.conversation_id
     WHERE e.created_at >= :start AND e.created_at < :end
     ORDER BY e.conversation_id, e.seq
""")

_RESERVATIONS = text("""
    SELECT id AS reservation_id, provider, company_id, location_id, party_size,
           start_at, end_at, status, source, conversation_id, diner_phone,
           created_at, updated_at
      FROM reservations
     WHERE created_at >= :start AND created_at < :end
    UNION ALL
    SELECT id, 'mock_external', company_id, location_id, party_size,
           start_at, end_at, status, NULL, NULL, diner_phone,
           created_at, updated_at
      FROM mock_external_bookings
     WHERE created_at >= :start AND created_at < :end
     ORDER BY created_at, reservation_id
""")

_AGENT_USAGE = text("""
    SELECT id AS usage_id, company_id, ownership_group_id, location_id,
           conversation_id, product, cost_usd, billed_usd, period_start,
           created_at
      FROM agent_usage
     WHERE created_at >= :start AND created_at < :end
     ORDER BY created_at, id
""")

_SCREENED_CALLS = text("""
    SELECT id AS screened_call_id, company_id, location_id, caller_key,
           decision, reason, created_at
      FROM screened_calls
     WHERE created_at >= :start AND created_at < :end
     ORDER BY created_at, id
""")


def window(day: date) -> tuple[datetime, datetime]:
    """[day 00:00Z, day+1 00:00Z)."""
    start = datetime.combine(day, time.min, tzinfo=timezone.utc)
    return start, start + timedelta(days=1)


def skipped(extracted: dict[str, list[dict[str, Any]]]) -> list[str]:
    """The lake tables `day` did not extract (eval_results without an
    evals-runs directory)."""
    return [t for t in TABLES if t not in extracted]


async def day(db: AsyncSession, day: date, *,
              runs_dir: Path | None = RUNS_DIR) -> dict[str, list[dict[str, Any]]]:
    """Every lake table's redacted rows for UTC `day`. `eval_results` is
    absent when `runs_dir` is None or does not exist."""
    start, end = window(day)
    params = {"start": start, "end": end}
    out: dict[str, list[dict[str, Any]]] = {}
    async with cross_tenant_session(db) as owner:
        await owner.execute(text("SET TRANSACTION READ ONLY"))
        try:
            async def rows(query: Any) -> list[dict[str, Any]]:
                return [dict(r) for r in (await owner.execute(query, params)).mappings()]

            out["conversations"] = await rows(_CONVERSATIONS)
            out["events"] = _events(await rows(_EVENTS))
            out["reservations"] = [_reservation(r) for r in await rows(_RESERVATIONS)]
            out["agent_usage"] = await rows(_AGENT_USAGE)
            out["screened_calls"] = await rows(_SCREENED_CALLS)
        finally:
            await owner.rollback()
    evals = eval_results(day, runs_dir)
    if evals is None:
        logger.info("analytics: no evals-runs directory, eval_results skipped for %s", day)
    else:
        out["eval_results"] = evals
    return out


def _reservation(r: dict[str, Any]) -> dict[str, Any]:
    phone = r.pop("diner_phone")
    r["diner_key"] = source_key_for(phone) if phone else None
    return r


def _label(v: Any) -> str | None:
    """An app-defined label (an action name, its result), redacted and
    capped: the action name comes from the client."""
    if not isinstance(v, str):
        return None
    return redact.text(v[:_LABEL_MAX])


def _texts(content: Any, *, skip_context: bool = False) -> str | None:
    if not isinstance(content, list):
        return None
    parts = [b.get("text", "") for b in content
             if isinstance(b, dict) and b.get("type") == "text"
             and isinstance(b.get("text"), str) and b.get("text")]
    if skip_context:
        parts = [p for p in parts if not p.startswith(_CONTEXT_PREFIX)]
    return redact.text("\n".join(parts)) if parts else None


def _ms(latency: Any, key: str) -> int | None:
    v = latency.get(key) if isinstance(latency, dict) else None
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return int(round(v))


def _tool_results(payload: dict[str, Any], tool_names: dict[str, str]) -> list[dict[str, Any]]:
    out = []
    for block in payload.get("content") or []:
        if not isinstance(block, dict):
            continue
        try:
            result = json.loads(block.get("content") or "{}")
        except (TypeError, ValueError):
            result = {}
        red = redact.payload(result if isinstance(result, dict) else {},
                             tool=tool_names.get(str(block.get("tool_use_id"))))
        red.setdefault("outcome", "error" if block.get("is_error") else "ok")
        out.append(red)
    return out


def _events(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    tool_names: dict[str, str] = {}  # tool_use_id -> tool name, from tool_calls
    for r in rows:
        p = r["payload"] if isinstance(r["payload"], dict) else {}
        if r["type"] == "tool_call" and p.get("tool_use_id") and isinstance(p.get("name"), str):
            tool_names[str(p["tool_use_id"])] = p["name"]

    out = []
    for r in rows:
        p = r.pop("payload")
        p = p if isinstance(p, dict) else {}
        latency = r.pop("latency_ms")
        r["llm_latency_ms"] = _ms(latency, "llm")
        r["tools_latency_ms"] = _ms(latency, "tools")
        body: Any = None
        words: str | None = None
        kind = r["type"]
        if p.get("erased"):
            pass  # erased mid-turn: the event keeps its cost, never its words
        elif kind == "user_turn":
            words = _texts(p.get("content"), skip_context=True)
        elif kind == "assistant_turn":
            words = _texts(p.get("content"))
            body = {"outcome": _label(p.get("stop_reason"))}
        elif kind == "tool_call":
            name = p.get("name") if isinstance(p.get("name"), str) else None
            body = redact.payload(p.get("input") if isinstance(p.get("input"), dict) else {},
                                  tool=name)
        elif kind == "tool_result":
            body = _tool_results(p, tool_names)
        elif kind == "error":
            body = redact.payload(p)
        elif kind == "handoff":
            words = redact.text(p.get("reason")) if isinstance(p.get("reason"), str) else None
        elif kind == "action":
            words = redact.text(p.get("reply")) if isinstance(p.get("reply"), str) else None
            body = {"name": _label(p.get("action")), "outcome": _label(p.get("result"))}
        r["text"] = words
        r["payload"] = json.dumps(body, sort_keys=True) if body is not None else None
        out.append(r)
    return out


def _parse_ts(v: Any) -> datetime | None:
    if not isinstance(v, str):
        return None
    try:
        ts = datetime.fromisoformat(v)
    except ValueError:
        return None
    return ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts.astimezone(timezone.utc)


def eval_results(day: date, runs_dir: Path | None = RUNS_DIR) -> list[dict[str, Any]] | None:
    """Rows of the eval run files whose run date (created_at, UTC) is `day`,
    or None when there is no runs directory. Scenario snapshots are left
    out; failure strings are redacted."""
    if runs_dir is None or not runs_dir.is_dir():
        return None
    out: list[dict[str, Any]] = []
    for path in sorted(runs_dir.glob("*.json")):
        try:
            doc = json.loads(path.read_text())
        except (OSError, ValueError):
            logger.warning("analytics: unreadable eval run file %s", path.name)
            continue
        created = _parse_ts(doc.get("created_at")) if isinstance(doc, dict) else None
        if created is None or created.date() != day:
            continue
        meta = doc.get("meta") if isinstance(doc.get("meta"), dict) else {}
        for res in doc.get("results") or []:
            if not isinstance(res, dict) or not res.get("scenario_id"):
                continue
            failures = [redact.text(str(f)) for f in res.get("failures") or []]
            out.append({
                "run_id": str(doc.get("id") or path.stem),
                "run_created_at": created,
                "git_sha": meta.get("git_sha"),
                "agent_model": meta.get("agent_model"),
                "mode": meta.get("mode"),
                "recognizer": meta.get("recognizer"),
                "prompt_version": meta.get("prompt_version"),
                "scenario_id": str(res["scenario_id"]),
                "attempt": int(res.get("attempt") or 0),
                "passed": bool(res.get("passed")),
                "failures": json.dumps(failures),
                "metrics": json.dumps(res.get("metrics") or {}, sort_keys=True,
                                      default=_json_default),
            })
    return out


def _json_default(v: Any) -> Any:
    if isinstance(v, Decimal):
        return float(v)
    return str(v)
