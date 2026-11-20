"""Data-quality checks on one extracted, landed day (#259 step 10). An empty
list passes; each failure is one line of counts and, at most, conversation
ids (never diner data), fit for the operator alert.

1. landed_count: every landed table's row count equals what was extracted.
2. negative_latency: no event has a negative llm or tools latency.
3. booked_ref: every conversation with outcome `booked` has a reservation
   ref that resolves through its own provider (reservations for
   wizscheduler, mock_external_bookings for the mock).
4. billing, per conversation, on matching rows from independent sources,
   all in the extract's snapshot (extract.RECONCILE), for the day's
   conversations and the day's agent_usage rows whatever day their
   counterpart was written (usage is written when a conversation is
   metered, possibly after midnight, so day sums are never compared):
   a. usage_cost: agent_usage.cost_usd equals the conversation's own
      agent_conversations.cost_usd;
   b. usage_billed: agent_usage.billed_usd equals
      subscription.billed_amount(agent_usage.cost_usd), the billing
      formula; usage_unexpected: a blocked conversation, or one whose cost
      bills to 0.00, has no usage row (subscription.meter never writes one);
   c. usage_orphan: every landed agent_usage row references a conversation
      that exists.
   billed_amount reads the current RESERVATIONS_USAGE_MARKUP, not the
   markup a row was billed at: after a markup change, rerunning or
   backfilling a day from before it fails usage_billed / usage_unexpected
   and withholds that day's metrics although nothing is wrong. Backfill
   before changing the markup, or accept the alert for those days.
   A conversation with cost and no usage row is not a defect (metering
   skips a company without an ownership group). This is an internal
   reconciliation; comparing against Stripe stays in the step 5 reconcile
   job (scripts/run_agent_overage_reconcile.py).

Check 3 reads the source DB (as the table owner) after the extract's
snapshot closed; it only looks up rows by id.
"""

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.analytics.extract import RECONCILE
from backend.analytics.schemas import TABLES
from backend.database import cross_tenant_session
from backend.reservations.subscription import billed_amount

MAX_IDS = 20

_REF_TABLE = {"wizscheduler": "reservations", "mock_external": "mock_external_bookings"}


def _ids(ids: list[str]) -> str:
    shown = ", ".join(sorted(ids)[:MAX_IDS])
    more = len(ids) - MAX_IDS
    return shown + (f" (+{more} more)" if more > 0 else "")


def landed_counts(extracted: dict[str, list[dict[str, Any]]],
                  landed: dict[str, int]) -> list[str]:
    out = []
    for t in TABLES:
        if t not in extracted:
            continue
        n, got = len(extracted[t]), landed.get(t)
        if got != n:
            shown = ("nothing" if got is None else "an unreadable file" if got < 0
                     else str(got))
            out.append(f"landed_count: {t} landed {shown} of {n} extracted rows")
    return out


def negative_latencies(extracted: dict[str, list[dict[str, Any]]]) -> list[str]:
    bad = [e for e in extracted.get("events", [])
           if any((e.get(k) or 0) < 0 for k in ("llm_latency_ms", "tools_latency_ms"))]
    if not bad:
        return []
    return [f"negative_latency: {len(bad)} event(s) in conversation(s) "
            f"{_ids(sorted({e['conversation_id'] for e in bad}))}"]


async def _existing(owner: AsyncSession, table: str, ids: list[str]) -> set[str]:
    if not ids:
        return set()
    rows = await owner.execute(text(f"SELECT id FROM {table} WHERE id = ANY(:ids)"),
                               {"ids": ids})
    return {r[0] for r in rows}


async def booked_refs(extracted: dict[str, list[dict[str, Any]]],
                      owner: AsyncSession) -> list[str]:
    booked = [c for c in extracted.get("conversations", []) if c.get("outcome") == "booked"]
    unresolved = [c["conversation_id"] for c in booked
                  if not c.get("reservation_id") or c.get("provider") not in _REF_TABLE]
    for provider, table in _REF_TABLE.items():
        refs = {c["conversation_id"]: c["reservation_id"] for c in booked
                if c.get("provider") == provider and c.get("reservation_id")}
        found = await _existing(owner, table, sorted(set(refs.values())))
        unresolved += [cid for cid, ref in refs.items() if ref not in found]
    if not unresolved:
        return []
    return [f"booked_ref: {len(unresolved)} of {len(booked)} booked conversation(s) have no "
            f"reservation that resolves through their provider: {_ids(unresolved)}"]


def billing(extracted: dict[str, list[dict[str, Any]]]) -> list[str]:
    """Checks 4a-4c over extract.RECONCILE (one snapshot, independent
    sources): the usage row's cost against the conversation's own cost, its
    billed amount against the billing formula, no usage for a blocked or
    zero-billed conversation, and no landed usage row without its
    conversation."""
    pairs = extracted.get(RECONCILE, [])
    cost_off, billed_off, unexpected = [], [], []
    orphans = 0
    for p in pairs:
        if not p["has_usage"]:
            continue
        if not p["conversation_exists"]:
            orphans += bool(p["usage_landed"])
            continue
        cid = p["conversation_id"]
        conv_cost = Decimal(p["conversation_cost_usd"] or 0)
        usage_cost = Decimal(p["usage_cost_usd"])
        if p["outcome"] == "blocked" or conv_cost <= 0 or billed_amount(conv_cost) <= 0:
            unexpected.append(cid)
            continue
        if usage_cost != conv_cost:
            cost_off.append(cid)
        if Decimal(p["usage_billed_usd"]) != billed_amount(usage_cost):
            billed_off.append(cid)
    usage_rows = sum(1 for p in pairs if p["has_usage"])
    out = []
    if cost_off:
        out.append(f"usage_cost: {len(cost_off)} of {usage_rows} usage row(s) disagree with "
                   f"their conversation's cost: {_ids(cost_off)}")
    if billed_off:
        out.append(f"usage_billed: {len(billed_off)} of {usage_rows} usage row(s) are not "
                   f"billed_amount(cost): {_ids(billed_off)}")
    if unexpected:
        out.append(f"usage_unexpected: {len(unexpected)} blocked or zero-cost conversation(s) "
                   f"have a usage row: {_ids(unexpected)}")
    if orphans:
        out.append(f"usage_orphan: {orphans} landed usage row(s) reference no conversation")
    return out


async def check(day: date, extracted: dict[str, list[dict[str, Any]]],
                landed: dict[str, int], db: AsyncSession) -> list[str]:
    """Every failure for `day`; empty when the day passes."""
    failures = (landed_counts(extracted, landed) + negative_latencies(extracted)
                + billing(extracted))
    async with cross_tenant_session(db) as owner:
        try:
            failures += await booked_refs(extracted, owner)
        finally:
            await owner.rollback()
    return failures
