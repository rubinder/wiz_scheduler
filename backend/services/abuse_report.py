"""Weekly report of ownership groups that look like the same operator.

Companion to services/signup_signals.py, which records the signals. This
reads them and clusters, so the question "is anyone actually farming free
tiers?" has an answer.

It REPORTS. It does not delete, suspend, or block anything, and nothing
downstream acts on its output automatically. Every signal here has an
innocent explanation — franchisees on one corporate network, a chain
whose branches share a name, a consultant setting up several clients from
one laptop — so a human decides, with the cluster in front of them.

The signals, weakest to strongest:

  * masked IP alone: nearly worthless. utils.privacy.mask_ip zeroes the
    last TWO bytes, so this is a /16 — an ISP region, not an address. It
    corroborates; it never accuses.
  * same normalized company name AND same /16: strong. Two unrelated
    businesses sharing both a name and a network region is a coincidence
    worth a human look. This is the pairing the report leads with.
  * same device id: strongest available. One browser, several signups.
    Defeated by a private window, which is why it is not the only signal.
  * same normalized email: catches `me+1@`, `m.e@`.

Paid groups are excluded outright: a customer is not a suspected account,
and "might be up for deletion" must never point at someone's live data.
The shared demo is excluded for the same reason it is excluded everywhere.
"""
from __future__ import annotations

import logging
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, TypedDict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models.ownership_group import OwnershipGroup
from backend.services.billing import is_demo_group

logger = logging.getLogger(__name__)

# Below this, a "cluster" is one account and there is nothing to look at.
MIN_CLUSTER = 2

_PUNCT = re.compile(r"[^a-z0-9]+")
# Suffixes that distinguish nothing: "Acme LLC" and "Acme Inc" are the same
# name for this purpose, and an operator minting accounts tends to vary them.
_SUFFIXES = {
    "llc", "inc", "ltd", "limited", "corp", "corporation", "co",
    "company", "gmbh", "bv", "sa", "srl", "plc", "lp", "llp",
}


def normalize_company_name(name: str) -> str:
    """Canonical form for comparing company names.

    Lowercased, punctuation-stripped, trailing legal suffixes removed:
    "Acme Co." / "acme llc" / "ACME, Inc" all collapse to "acme".
    """
    words = _PUNCT.sub(" ", (name or "").lower()).split()
    while words and words[-1] in _SUFFIXES:
        words.pop()
    return " ".join(words)


class SuspectAccount(TypedDict):
    ownership_group_id: str
    name: str
    created_at: str | None
    signup_ip_masked: str | None
    signup_email_normalized: str | None
    signup_device_id: str | None


class SuspectCluster(TypedDict):
    signal: str
    value: str
    confidence: str
    accounts: list[SuspectAccount]


def _account(og: OwnershipGroup) -> SuspectAccount:
    return SuspectAccount(
        ownership_group_id=str(og.id),
        name=og.name,
        created_at=og.created_at.isoformat() if og.created_at else None,
        signup_ip_masked=og.signup_ip_masked,
        signup_email_normalized=og.signup_email_normalized,
        signup_device_id=og.signup_device_id,
    )


async def build_suspected_accounts_report(
    db: AsyncSession, *, window_days: int = 90
) -> dict[str, Any]:
    """Cluster free ownership groups that share signup signals.

    *window_days* bounds how far back to look. Signals age out of the
    database entirely after RETENTION_SIGNUP_SIGNALS_DAYS, so a window
    wider than that finds nothing extra.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)

    groups = (await db.execute(
        select(OwnershipGroup).where(OwnershipGroup.created_at >= cutoff)
    )).scalars().all()

    candidates = [
        og for og in groups
        # A paying customer is not a suspected account, and this report must
        # never point "up for deletion" at someone's live data.
        if not (og.stripe_subscription_id is not None and og.canceled_at is None)
        and not is_demo_group(str(og.id))
    ]

    by_name_and_ip: dict[tuple, list] = defaultdict(list)
    by_device: dict[str, list] = defaultdict(list)
    by_email: dict[str, list] = defaultdict(list)

    for og in candidates:
        if og.name and og.signup_ip_masked:
            by_name_and_ip[
                (normalize_company_name(og.name), og.signup_ip_masked)
            ].append(og)
        if og.signup_device_id:
            by_device[og.signup_device_id].append(og)
        if og.signup_email_normalized:
            by_email[og.signup_email_normalized].append(og)

    clusters: list[SuspectCluster] = []

    for (name, ip), members in sorted(by_name_and_ip.items()):
        if len(members) >= MIN_CLUSTER and name:
            clusters.append(SuspectCluster(
                signal="company_name_and_ip",
                value=f"{name} @ {ip}",
                confidence="high",
                accounts=[_account(og) for og in members],
            ))

    for device_id, members in sorted(by_device.items()):
        if len(members) >= MIN_CLUSTER:
            clusters.append(SuspectCluster(
                signal="device_id",
                value=device_id,
                confidence="high",
                accounts=[_account(og) for og in members],
            ))

    for email, members in sorted(by_email.items()):
        if len(members) >= MIN_CLUSTER:
            clusters.append(SuspectCluster(
                signal="email_normalized",
                # The address is the finding; it is already in the row this
                # report is about, so nothing new is exposed by naming it.
                value=email,
                confidence="medium",
                accounts=[_account(og) for og in members],
            ))

    flagged = {
        acct["ownership_group_id"]
        for cluster in clusters
        for acct in cluster["accounts"]
    }

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window_days": window_days,
        "groups_examined": len(candidates),
        "clusters": clusters,
        "accounts_flagged": len(flagged),
        # Said in the payload, not just the docs, because this is what a
        # reader acts on: the report proposes a look, never a deletion.
        "note": (
            "Suspected only. Every signal here has innocent explanations "
            "(franchisees behind one corporate network, a chain sharing a "
            "name, one consultant onboarding several clients). Masked IPs "
            "are /16 — an ISP region, not an address. Review before acting; "
            "nothing is deleted automatically."
        ),
    }

    logger.info(
        "abuse_report.generated groups=%d clusters=%d flagged=%d",
        len(candidates), len(clusters), len(flagged),
    )
    return report
