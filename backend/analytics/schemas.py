"""The lake's tables: one fixed pyarrow schema per Parquet dataset (#259
step 10). This module is the single source of the columns; the Glue table
definitions are generated from it, so change a column here and nowhere
else. The partition column `dt` lives in the path, not in these schemas.

Timestamps are UTC microseconds, money is decimal(12, 6), and JSON-ish
values are strings holding JSON.

The lake is anonymous: no column holds a diner's name, phone number, email,
confirmation code or free text, and none holds a pseudonymous diner key
(the HMAC source_key / caller_key, or one derived from a phone). No metric
needs diner identity, and with no key in the lake a GDPR erasure needs no
lake rewrite.
"""

import pyarrow as pa

TS = pa.timestamp("us", tz="UTC")
MONEY = pa.decimal128(12, 6)


def _f(name: str, type_: pa.DataType, nullable: bool = True) -> pa.Field:
    return pa.field(name, type_, nullable=nullable)


CONVERSATIONS = pa.schema([
    _f("conversation_id", pa.string(), False),
    _f("company_id", pa.string(), False),
    _f("location_id", pa.string(), False),
    _f("channel", pa.string(), False),
    _f("provider", pa.string(), False),
    _f("status", pa.string(), False),
    _f("outcome", pa.string()),
    _f("reservation_id", pa.string()),
    _f("turn_count", pa.int32(), False),
    _f("failed_lookups", pa.int32(), False),
    _f("cost_usd", MONEY, False),
    # agent_usage.billed_usd for this conversation; NULL until it is billed.
    # The source is numeric(12, 2), which fits decimal(12, 6) up to
    # $999,999.99 (plenty for one conversation).
    _f("billed_usd", MONEY),
    _f("started_at", TS, False),
    _f("ended_at", TS),
])

EVENTS = pa.schema([
    _f("event_id", pa.string(), False),
    _f("conversation_id", pa.string(), False),
    _f("company_id", pa.string(), False),
    _f("location_id", pa.string(), False),
    _f("seq", pa.int32(), False),
    _f("type", pa.string(), False),
    # Length of the free text the event carried (a turn's words, an action's
    # reply, a handoff reason that is not a known code). The text itself
    # never lands.
    _f("text_chars", pa.int32()),
    # JSON: the allowlisted tool payload (redact.payload); a list of them
    # for a tool_result; {"name", "outcome"} for an action (known labels
    # only); {"reason"} for a handoff (a known code or "other");
    # {"outcome": stop_reason} for an assistant turn.
    _f("payload", pa.string()),
    _f("llm_latency_ms", pa.int64()),
    _f("tools_latency_ms", pa.int64()),
    _f("input_tokens", pa.int32(), False),
    _f("output_tokens", pa.int32(), False),
    _f("cache_read_tokens", pa.int32(), False),
    _f("cache_write_tokens", pa.int32(), False),
    _f("cost_usd", MONEY, False),
    _f("created_at", TS, False),
])

RESERVATIONS = pa.schema([
    _f("reservation_id", pa.string(), False),
    _f("provider", pa.string(), False),
    _f("company_id", pa.string(), False),
    _f("location_id", pa.string(), False),
    _f("party_size", pa.int32(), False),
    _f("start_at", TS, False),
    _f("end_at", TS, False),
    _f("status", pa.string(), False),
    # WizScheduler's source (staff, web_agent, voice_agent); NULL for the mock.
    _f("source", pa.string()),
    _f("conversation_id", pa.string()),
    _f("created_at", TS, False),
    _f("updated_at", TS, False),
])

AGENT_USAGE = pa.schema([
    _f("usage_id", pa.string(), False),
    _f("company_id", pa.string(), False),
    _f("ownership_group_id", pa.string(), False),
    _f("location_id", pa.string(), False),
    _f("conversation_id", pa.string()),
    _f("product", pa.string(), False),
    _f("cost_usd", MONEY, False),
    _f("billed_usd", MONEY, False),  # numeric(12, 2) at the source; fits
    _f("period_start", TS, False),
    _f("created_at", TS, False),
])

SCREENED_CALLS = pa.schema([
    _f("screened_call_id", pa.string(), False),
    _f("company_id", pa.string(), False),
    _f("location_id", pa.string(), False),
    _f("decision", pa.string(), False),
    _f("reason", pa.string(), False),
    _f("created_at", TS, False),
])

EVAL_RESULTS = pa.schema([
    _f("run_id", pa.string(), False),
    _f("run_created_at", TS, False),
    _f("git_sha", pa.string()),
    _f("agent_model", pa.string()),
    _f("mode", pa.string()),
    _f("recognizer", pa.string()),
    _f("prompt_version", pa.string()),
    _f("scenario_id", pa.string(), False),
    _f("attempt", pa.int32(), False),
    _f("passed", pa.bool_(), False),
    # JSON list of failure categories (extract.failure_category), never the
    # failure text, which can quote the diner's name and phone.
    _f("failures", pa.string()),
    _f("failure_count", pa.int32(), False),
    _f("metrics", pa.string()),  # JSON object of extract.EVAL_METRIC_KEYS only
])

TABLES: dict[str, pa.Schema] = {
    "conversations": CONVERSATIONS,
    "events": EVENTS,
    "reservations": RESERVATIONS,
    "agent_usage": AGENT_USAGE,
    "screened_calls": SCREENED_CALLS,
    "eval_results": EVAL_RESULTS,
}
