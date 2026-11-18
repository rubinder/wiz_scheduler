"""Toll-fraud bounds on the AI host's staff transfer (#259 step 6).

A handoff transfers the call to the location's staff number, an outbound
call from Amazon Connect billed per minute. So:

- the number must be a valid US or Canadian NANP number (`nanp_problem`):
  `+1` with a 10-digit number whose area code is not another NANP
  country's, not premium rate, and not invalid; and it must not be any AI
  host number we hold (`number_problem`, read across tenants: calling one
  would loop a call through Connect on our bill). Checked when the rules
  are saved and again before every transfer.
- a location transfers at most VOICE_TRANSFERS_PER_DAY calls per local
  day (`reserve`): an atomic counter per location and local date in
  voice_transfer_counts, which diner-data erasure never touches.
"""

import re
from datetime import date

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import cross_tenant_session
from backend.reservations.voice_number_models import VoiceNumber, VoiceTransferCount

# Area codes of the other NANP countries and territories: calls there are
# billed internationally (or, for the US territories, we are not sure they
# are domestic-rated, so they are refused too). Source: NANPA, "NANP
# countries" / area code map (nationalnanpa.com). Review when NANPA
# assigns a new overlay to one of these.
NON_US_CA_NPAS = frozenset({
    "242",         # Bahamas
    "246",         # Barbados
    "264",         # Anguilla
    "268",         # Antigua and Barbuda
    "284",         # British Virgin Islands
    "340",         # US Virgin Islands (territory)
    "345",         # Cayman Islands
    "441",         # Bermuda
    "473",         # Grenada
    "649",         # Turks and Caicos
    "658", "876",  # Jamaica
    "664",         # Montserrat
    "670",         # Northern Mariana Islands (territory)
    "671",         # Guam (territory)
    "684",         # American Samoa (territory)
    "721",         # Sint Maarten
    "758",         # Saint Lucia
    "767",         # Dominica
    "784",         # Saint Vincent and the Grenadines
    "787", "939",  # Puerto Rico (territory)
    "809", "829", "849",  # Dominican Republic
    "868",         # Trinidad and Tobago
    "869",         # Saint Kitts and Nevis
})

# Premium-rate and non-geographic service codes that may bill the caller:
# 900 and 976 premium rate, 456 international inbound, 500-series personal
# communications, 700 carrier services, 710 US government.
BLOCKED_NPAS = frozenset({"900", "976", "456", "500", "521", "522", "523",
                          "524", "525", "526", "527", "528", "529", "533",
                          "544", "566", "577", "588", "700", "710"})
# The old premium-rate exchange inside ordinary area codes.
BLOCKED_NXX = frozenset({"976"})

_NANP = re.compile(r"\+1(?P<npa>[2-9][0-8][0-9])(?P<nxx>[2-9][0-9]{2})[0-9]{4}")


def nanp_problem(number: str) -> str | None:
    """Why `number` is not a US/Canadian number we may transfer to, or None."""
    m = _NANP.fullmatch(number)
    if m is None:
        return "staff_transfer_number must be a US or Canadian number (+1 and 10 digits)"
    npa, nxx = m["npa"], m["nxx"]
    if npa[1:] == "11" or nxx[1:] == "11":
        return "staff_transfer_number is not a valid number"
    if npa in NON_US_CA_NPAS:
        return "staff_transfer_number must be a US or Canadian number"
    if npa in BLOCKED_NPAS or nxx in BLOCKED_NXX:
        return "staff_transfer_number cannot be a premium-rate or service number"
    return None


async def number_problem(db: AsyncSession, number: str) -> str | None:
    """`nanp_problem`, and the number is not one of our AI host numbers
    (any status, any tenant)."""
    problem = nanp_problem(number)
    if problem:
        return problem
    async with cross_tenant_session(db) as everyone:
        ours = (await everyone.execute(
            select(VoiceNumber.id).where(VoiceNumber.e164 == number).limit(1)
        )).first()
    if ours is not None:
        return "staff_transfer_number cannot be an AI host number"
    return None


async def reserve(db: AsyncSession, company_id: str, location_id: str,
                  local_date: date, cap: int) -> bool:
    """Take one of the location's `cap` transfers for `local_date`. One
    statement: a concurrent reservation waits on the row and then sees the
    new count, so the cap holds under races. Does not commit; the row stays
    locked until the caller commits."""
    stmt = insert(VoiceTransferCount).values(
        company_id=company_id, location_id=location_id, local_date=local_date, count=1)
    stmt = stmt.on_conflict_do_update(
        index_elements=[VoiceTransferCount.location_id, VoiceTransferCount.local_date],
        set_={"count": VoiceTransferCount.count + 1},
        where=VoiceTransferCount.count < cap,
    ).returning(VoiceTransferCount.count)
    if cap < 1:
        return False
    return (await db.execute(stmt)).first() is not None
