"""Daily AI host metrics per location (#259 step 10), computed purely from
the rows extract.day returned, the same rows the lake lands, so the
metrics and the lake agree.

Which rows feed what:

- conversations, outcomes, cost_usd, billed_usd, booking_intent: the
  conversations that started that UTC day (extract's conversations, as of
  the snapshot; the job re-runs each day once more the next night, so a
  conversation open at midnight gets its final state). billed_usd is the
  conversation's agent_usage billed amount, whenever it was billed.
- booking_intent: those of them with a find_slots tool_call in any of
  their events, whatever day it was written (extract.INTENT), so booking
  conversion never exceeds 1.
- handoff_reasons and latencies: the events created that UTC day.
  turn latency is the model plus tool time of one diner turn: the
  llm and tools latencies of the events from a user_turn up to the next
  user_turn or action of that conversation. The wall clock a diner waits
  (as the eval runner's turn_ms) is not recorded in production.
- screened, blocked: the screened calls of that UTC day.
- covers_by_period: extract.COVERS, booked reservations of either provider
  whose start falls on `day` as a local service day (the location's time
  zone, not UTC), by the rules' service period whose seating window holds
  the start time ("other" if none does). A local day ends up to a day after
  the UTC one, and bookings keep changing until it starts, so the figure
  is final only on the D-2 pass of the nightly job.

Derived rates are not stored: resolution rate = resolved / conversations,
booking conversion = booked / booking_intent, spam-blocked share =
blocked / screened. Percentiles use the nearest rank method.
"""

import json
import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from backend.analytics.extract import COVERS, INTENT
from backend.reservations.rules import RulesSchema

RESOLVED_OUTCOMES = ("booked", "modified", "cancelled")
COUNTED_OUTCOMES = ("booked", "handed_off", "abandoned", "failed")
OTHER_PERIOD = "other"
TURN_STARTS = ("user_turn", "action")


@dataclass(frozen=True)
class LocationRules:
    """What compute needs of a location: its rules and its time zone."""
    rules: RulesSchema
    tz: ZoneInfo


def local_zone(name: str | None) -> ZoneInfo:
    """The location's zone, UTC when the name is missing or unknown."""
    try:
        return ZoneInfo(name or "UTC")
    except (KeyError, ValueError):
        return ZoneInfo("UTC")


def percentile(values: Iterable[float], p: float) -> int | None:
    """Nearest rank: the smallest value with at least p% of values at or
    below it. None for no values."""
    vals = sorted(values)
    if not vals:
        return None
    rank = max(1, math.ceil(p / 100 * len(vals)))
    return int(round(vals[rank - 1]))


def period_name(rules: RulesSchema | None, local: datetime) -> str:
    """The service period whose seating window [first_seating, last_seating]
    holds `local`'s time on its date, else OTHER_PERIOD."""
    if rules is None:
        return OTHER_PERIOD
    t = local.timetz().replace(tzinfo=None)
    for p in rules.periods_for(local.date()):
        if p.first_seating <= t <= p.last_seating:
            return p.name
    return OTHER_PERIOD


def _payload(v: Any) -> Any:
    if not isinstance(v, str):
        return None
    try:
        return json.loads(v)
    except ValueError:
        return None


def _turn_latencies(events: list[dict[str, Any]]) -> list[int]:
    by_conv: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e in events:
        by_conv[e["conversation_id"]].append(e)
    out: list[int] = []
    for evs in by_conv.values():
        total: int | None = None
        for e in sorted(evs, key=lambda e: e["seq"]):
            if e["type"] in TURN_STARTS:
                if total is not None:
                    out.append(total)
                total = 0 if e["type"] == "user_turn" else None
                continue
            if total is not None:
                total += (e.get("llm_latency_ms") or 0) + (e.get("tools_latency_ms") or 0)
        if total is not None:
            out.append(total)
    return [t for t in out if t > 0]


def compute(extracted: dict[str, list[dict[str, Any]]],
            rules_by_location: dict[str, LocationRules], *,
            day: date) -> list[dict[str, Any]]:
    """One agent_daily_metrics row (as a dict of its columns, no id) per
    location with any conversation, event, screened call or covers on
    `day`, sorted by location id. Pure."""
    convs = extracted.get("conversations", [])
    events = extracted.get("events", [])
    screened = extracted.get("screened_calls", [])
    intent = {r["conversation_id"] for r in extracted.get(INTENT, [])}
    covers = []
    for r in extracted.get(COVERS, []):
        lr = rules_by_location.get(r["location_id"])
        start: datetime = r["start_at"]
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        local = start.astimezone(lr.tz if lr else local_zone(r.get("timezone")))
        if local.date() == day:
            covers.append({**r, "local_start": local})

    company: dict[str, str] = {}
    for r in (*convs, *events, *screened, *covers):
        company.setdefault(r["location_id"], r["company_id"])

    handoffs: dict[str, Counter[str]] = defaultdict(Counter)
    llm: dict[str, list[int]] = defaultdict(list)
    events_by_loc: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e in events:
        loc = e["location_id"]
        events_by_loc[loc].append(e)
        if e.get("llm_latency_ms") is not None:
            llm[loc].append(e["llm_latency_ms"])
        body = _payload(e.get("payload"))
        if e["type"] == "handoff":
            reason = body.get("reason") if isinstance(body, dict) else None
            handoffs[loc][reason if isinstance(reason, str) else "other"] += 1

    rows: list[dict[str, Any]] = []
    for loc in sorted(company):
        lc = [c for c in convs if c["location_id"] == loc]
        outcomes = Counter(c.get("outcome") for c in lc)
        turns = _turn_latencies(events_by_loc.get(loc, []))
        lr = rules_by_location.get(loc)
        cover_counts: Counter[str] = Counter()
        for r in covers:
            if r["location_id"] != loc:
                continue
            cover_counts[period_name(lr.rules if lr else None, r["local_start"])] \
                += int(r["party_size"])
        sc = [s for s in screened if s["location_id"] == loc]
        rows.append({
            "company_id": company[loc],
            "location_id": loc,
            "day": day,
            "conversations": len(lc),
            "resolved": sum(outcomes[o] for o in RESOLVED_OUTCOMES),
            **{o: outcomes[o] for o in COUNTED_OUTCOMES},
            "booking_intent": sum(1 for c in lc if c["conversation_id"] in intent),
            "handoff_reasons": dict(sorted(handoffs[loc].items())),
            "llm_p50_ms": percentile(llm[loc], 50),
            "llm_p95_ms": percentile(llm[loc], 95),
            "turn_p50_ms": percentile(turns, 50),
            "turn_p95_ms": percentile(turns, 95),
            "cost_usd": sum((Decimal(c["cost_usd"] or 0) for c in lc), Decimal("0")),
            "billed_usd": sum((Decimal(c["billed_usd"]) for c in lc
                               if c.get("billed_usd") is not None),
                              Decimal("0")).quantize(Decimal("0.01")),
            "screened": len(sc),
            "blocked": sum(1 for s in sc if s["decision"] == "block"),
            "covers_by_period": dict(sorted(cover_counts.items())),
        })
    return rows
