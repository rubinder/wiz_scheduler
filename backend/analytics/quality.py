"""Data-quality checks on one extracted, landed day (#259 step 10). An empty
list passes; each failure is one line of counts and, at most, conversation
ids (never diner data), fit for the operator alert.

1. landed_count: every landed table's row count equals what was extracted.
2. negative_latency: no event has a negative llm or tools latency.
3. booked_ref: every conversation with outcome `booked` has a reservation
   ref that resolves through its own provider (reservations for
   wizscheduler, mock_external_bookings for the mock).
4. billed: billed amounts reconcile on matching rows. For the day's
   conversations that had been billed at the snapshot, each one's
   billed_usd equals agent_usage.billed_usd for that conversation_id, read
   now (agent_usage may have been written after midnight, on another day's
   extract, so day sums of the two tables are never compared). The day's
   total over those conversations is reported. A conversation billed after
   the snapshot is not a defect: the next night's re-run picks it up. This
   is an internal reconciliation; comparing against Stripe stays in the
   step 5 reconcile job (scripts/run_agent_overage_reconcile.py).

Checks 3 and 4 read the source DB (as the table owner) after the extract's
snapshot closed; they only look up rows by id.
"""

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.analytics.schemas import TABLES
from backend.database import cross_tenant_session

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
            out.append(f"landed_count: {t} landed {got if got is not None else 'nothing'} "
                       f"of {n} extracted rows")
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


async def billed(extracted: dict[str, list[dict[str, Any]]],
                 owner: AsyncSession) -> list[str]:
    convs = {c["conversation_id"]: Decimal(c["billed_usd"])
             for c in extracted.get("conversations", []) if c.get("billed_usd") is not None}
    if not convs:
        return []
    rows = await owner.execute(text(
        "SELECT conversation_id, sum(billed_usd) FROM agent_usage "
        "WHERE conversation_id = ANY(:ids) GROUP BY conversation_id"), {"ids": sorted(convs)})
    usage = {r[0]: Decimal(r[1]) for r in rows}
    mismatched = [cid for cid, amount in convs.items() if usage.get(cid) != amount]
    if not mismatched:
        return []
    conv_total = sum(convs.values(), Decimal("0"))
    usage_total = sum(usage.values(), Decimal("0"))
    return [f"billed: {len(mismatched)} of {len(convs)} billed conversation(s) disagree with "
            f"agent_usage (conversations ${conv_total:.2f}, agent_usage ${usage_total:.2f}): "
            f"{_ids(mismatched)}"]


async def check(day: date, extracted: dict[str, list[dict[str, Any]]],
                landed: dict[str, int], db: AsyncSession) -> list[str]:
    """Every failure for `day`; empty when the day passes."""
    failures = landed_counts(extracted, landed) + negative_latencies(extracted)
    async with cross_tenant_session(db) as owner:
        try:
            failures += await booked_refs(extracted, owner)
            failures += await billed(extracted, owner)
        finally:
            await owner.rollback()
    return failures
