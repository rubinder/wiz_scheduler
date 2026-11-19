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

The lake is anonymous (see schemas.py). Names, emails, notes, phones,
caller numbers and the pseudonymous HMAC keys (source_key, caller_key) are
never selected, so a GDPR erasure needs no lake rewrite. Free text never
lands: turns, action replies and free-form handoff reasons become a
character count. Tool payloads go through redact.payload; the remaining
machine strings (error codes, labels) through redact.text, which first
replaces the day's known confirmation codes exactly.

Snapshot semantics: a day is read once. A conversation still open when its
start day is extracted lands with its status, outcome, cost and ended_at as
of that moment, and is not revisited unless that day is rerun.

Note for the metrics (task 2): agent_usage rows and conversations can land
on different days (usage is written when a conversation is billed, possibly
after midnight), so reconcile billed amounts on matching rows
(agent_usage.conversation_id = conversations.conversation_id), not on day
sums.
"""

import json
import logging
import re
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.analytics import redact
from backend.analytics.schemas import TABLES
from backend.database import cross_tenant_session

logger = logging.getLogger(__name__)

# Where the eval runner writes its run files (reservations/evals/store.py,
# RUNS_DIR). Not imported from there: product code never imports the evals
# package (tests/test_architecture.py, product-imports-evals).
RUNS_DIR = Path(__file__).resolve().parents[2] / "evals-runs"

# The context line core.py puts before the diner's words in a user_turn.
_CONTEXT_PREFIX = "[Now:"
STATEMENT_TIMEOUT = "120s"

# The structured actions core.Agent._action handles; anything else the
# client sent lands as "unknown".
KNOWN_ACTIONS = frozenset({"confirm_hold", "decline_hold", "confirm_cancel", "confirm_modify"})
# Handoff reasons that are fixed codes (core.py, tools.py, the model's stop
# reasons); a free-form reason from handoff_to_staff lands as "other".
KNOWN_HANDOFF_REASONS = frozenset({
    "error", "llm_error", "round_cap", "lookup_lockout", "provider_unavailable",
    "large_party", "max_tokens", "stop_sequence", "pause_turn", "refusal",
    "model_context_window_exceeded", "tool_use", "end_turn",
})
# A machine label: lowercase words joined by underscores.
_LABEL = re.compile(r"[a-z][a-z0-9_]{0,39}")

# eval_results.metrics keeps only these scalars (the runner's lists, what a
# voice recognizer heard and its error strings never land; voice lists are
# reduced to counts).
EVAL_METRIC_KEYS = ("turns", "diner_turns", "model_calls", "total_cost_usd", "llm_p50_ms",
                    "llm_p95_ms", "turn_p50_ms", "turn_p95_ms", "handoff", "outcome",
                    "book_goal")
EVAL_VOICE_COUNTS = {"heard": "heard_count", "errors": "error_count",
                     "ssml_errors": "ssml_error_count", "repeats": "repeat_count"}
# checker._describe's `name='...'` and `phone=...` fragments.
_FAILURE_FIELD = re.compile(r"""\b(name|phone)=(?:'(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*"|\S+)""")

_CONVERSATIONS = text("""
    SELECT c.id AS conversation_id, c.company_id, c.location_id, c.channel,
           c.provider, c.status, c.outcome, c.reservation_id,
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

# A mock hold past its held_until (or with none) reads back as expired, as
# providers/mock_external._status has it; now() is the transaction's start.
_RESERVATIONS = text("""
    SELECT id AS reservation_id, provider, company_id, location_id, party_size,
           start_at, end_at, status, source, conversation_id,
           created_at, updated_at
      FROM reservations
     WHERE created_at >= :start AND created_at < :end
    UNION ALL
    SELECT id, 'mock_external', company_id, location_id, party_size,
           start_at, end_at,
           CASE WHEN status = 'held' AND (held_until IS NULL OR held_until <= now())
                THEN 'expired' ELSE status END,
           NULL, NULL, created_at, updated_at
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
    SELECT id AS screened_call_id, company_id, location_id,
           decision, reason, created_at
      FROM screened_calls
     WHERE created_at >= :start AND created_at < :end
     ORDER BY created_at, id
""")


# Codes a diner or the model may quote that day: those of bookings created
# or changed in the window, from both providers. Never landed; used only to
# mask them exactly in the strings that do.
_KNOWN_CODES = text("""
    SELECT confirmation_code AS code FROM reservations
     WHERE confirmation_code IS NOT NULL
       AND ((created_at >= :start AND created_at < :end)
            OR (updated_at >= :start AND updated_at < :end))
    UNION
    SELECT code FROM mock_external_bookings
     WHERE code IS NOT NULL
       AND ((created_at >= :start AND created_at < :end)
            OR (updated_at >= :start AND updated_at < :end))
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
        # One snapshot for every table, so counts agree across them.
        await owner.execute(text(
            "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        await owner.execute(text(f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT}'"))
        try:
            async def rows(query: Any) -> list[dict[str, Any]]:
                return [dict(r) for r in (await owner.execute(query, params)).mappings()]

            codes = tuple(r["code"] for r in await rows(_KNOWN_CODES))
            out["conversations"] = await rows(_CONVERSATIONS)
            out["events"] = _events(await rows(_EVENTS), codes)
            out["reservations"] = await rows(_RESERVATIONS)
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


def _label(v: Any, fallback: str) -> str:
    """A machine label (a stop reason, an action result's leading word):
    kept when it looks like one, else `fallback`. An action result can
    carry a code ("booked QX7K9P"), so only its first word is read."""
    word = v.split(maxsplit=1)[0] if isinstance(v, str) and v.strip() else ""
    return word if _LABEL.fullmatch(word) else fallback


def _chars(v: Any) -> int | None:
    return len(v) if isinstance(v, str) else None


def _text_chars(content: Any, *, skip_context: bool = False) -> int | None:
    """How many characters of text a turn's content blocks carried."""
    if not isinstance(content, list):
        return None
    parts = [b["text"] for b in content
             if isinstance(b, dict) and b.get("type") == "text"
             and isinstance(b.get("text"), str) and b.get("text")]
    if skip_context:
        parts = [p for p in parts if not p.startswith(_CONTEXT_PREFIX)]
    return sum(len(p) for p in parts) if parts else None


def _ms(latency: Any, key: str) -> int | None:
    v = latency.get(key) if isinstance(latency, dict) else None
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return int(round(v))


def _tool_results(payload: dict[str, Any], tool_names: dict[str, str],
                  codes: tuple[str, ...]) -> list[dict[str, Any]]:
    out = []
    for block in payload.get("content") or []:
        if not isinstance(block, dict):
            continue
        try:
            result = json.loads(block.get("content") or "{}")
        except (TypeError, ValueError):
            result = {}
        red = redact.payload(result if isinstance(result, dict) else {},
                             tool=tool_names.get(str(block.get("tool_use_id"))),
                             known_codes=codes)
        red.setdefault("outcome", "error" if block.get("is_error") else "ok")
        out.append(red)
    return out


def _events(rows: list[dict[str, Any]], codes: tuple[str, ...]) -> list[dict[str, Any]]:
    tool_names: dict[str, str] = {}  # tool_use_id -> tool name, from tool_calls
    for r in rows:
        p = r["payload"] if isinstance(r["payload"], dict) else {}
        if r["type"] == "tool_call" and p.get("tool_use_id"):
            tool_names[str(p["tool_use_id"])] = _label(p.get("name"), "unknown")

    out = []
    for r in rows:
        p = r.pop("payload")
        p = p if isinstance(p, dict) else {}
        latency = r.pop("latency_ms")
        r["llm_latency_ms"] = _ms(latency, "llm")
        r["tools_latency_ms"] = _ms(latency, "tools")
        body: Any = None
        chars: int | None = None
        kind = r["type"]
        if p.get("erased"):
            pass  # erased mid-turn: the event keeps its cost, never its words
        elif kind == "user_turn":
            chars = _text_chars(p.get("content"), skip_context=True)
        elif kind == "assistant_turn":
            chars = _text_chars(p.get("content"))
            body = {"outcome": _label(p.get("stop_reason"), "other")}
        elif kind == "tool_call":
            body = redact.payload(p.get("input") if isinstance(p.get("input"), dict) else {},
                                  tool=_label(p.get("name"), "unknown"), known_codes=codes)
        elif kind == "tool_result":
            body = _tool_results(p, tool_names, codes)
        elif kind == "error":
            body = redact.payload(p, known_codes=codes)
        elif kind == "handoff":
            reason = p.get("reason")
            if isinstance(reason, str) and reason in KNOWN_HANDOFF_REASONS:
                body = {"reason": reason}
            else:
                body, chars = {"reason": "other"}, _chars(reason)
        elif kind == "action":
            chars = _chars(p.get("reply"))
            action = p.get("action")
            body = {"name": action if action in KNOWN_ACTIONS else "unknown",
                    "outcome": _label(p.get("result"), "other")}
        r["text_chars"] = chars
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
            failures = [redact.text(_FAILURE_FIELD.sub(r"\1=[redacted]", str(f)))
                        for f in res.get("failures") or []]
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
                "metrics": json.dumps(eval_metrics(res.get("metrics")), sort_keys=True),
            })
    return out


def _metric(v: Any) -> Any:
    if isinstance(v, bool) or v is None:
        return v
    if isinstance(v, (int, float, Decimal)):
        return float(v) if isinstance(v, Decimal) else v
    if isinstance(v, str):
        return _label(v, "other")
    return None


def eval_metrics(metrics: Any) -> dict[str, Any]:
    """The allowlisted metrics of one eval result: EVAL_METRIC_KEYS as
    scalars, `error_count` for the checker's error list, and under `voice`
    only the recognizer and counts (EVAL_VOICE_COUNTS)."""
    m = metrics if isinstance(metrics, dict) else {}
    out: dict[str, Any] = {k: _metric(m[k]) for k in EVAL_METRIC_KEYS if k in m}
    if isinstance(m.get("errors"), list):
        out["error_count"] = len(m["errors"])
    voice = m.get("voice")
    if isinstance(voice, dict):
        v: dict[str, Any] = {}
        if "recognizer" in voice:
            v["recognizer"] = _metric(voice["recognizer"])
        for key, count in EVAL_VOICE_COUNTS.items():
            if isinstance(voice.get(key), list):
                v[count] = len(voice[key])
        out["voice"] = v
    return out
