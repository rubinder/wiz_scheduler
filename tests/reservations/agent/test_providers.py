"""Booking providers (#259 step 9): the agent books only through a
BookingProvider, and a conversation's provider is fixed when it starts."""

import ast
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from backend.reservations.agent import recording
from backend.reservations.providers import (
    HELD,
    NotFound,
    ProviderBooking,
    ProviderContext,
    ProviderError,
    ProviderSlot,
    SlotUnavailable,
    Transient,
    provider_for,
    provider_named,
)
from backend.reservations.providers.wizscheduler import WizSchedulerProvider
from backend.reservations.rules import get_rules, save_rules
from tests.conftest import COMPANY_ID, LOCATION_ID

NY = ZoneInfo("America/New_York")
PHONE = "+12125550100"
AGENT = Path(__file__).resolve().parents[3] / "backend" / "reservations" / "agent"

# The agent's booking operations go through the provider: no file in the
# agent package imports these modules, under any name (absolute or relative),
# or the `Reservation` model from `backend.models`.
_FORBIDDEN = ("backend.reservations.booking", "backend.reservations.availability",
              "backend.reservations.models")
_PACKAGE = "backend.reservations.agent"

# Agent files exempt from the scan, with the reason. Checked: no file in the
# package needs the reservation tables (the conversation tables live in
# agent/models.py and import only backend.database), so none is exempt.
_EXEMPT: dict[str, str] = {}


def _resolve(node: ast.ImportFrom) -> str | None:
    if node.level == 0:
        return node.module
    base = _PACKAGE.split(".")[: len(_PACKAGE.split(".")) - (node.level - 1)]
    return ".".join(base + ([node.module] if node.module else []))


def _violations(path: Path) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            mods = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            module = _resolve(node)
            if module is None:
                continue
            mods = [module] + [f"{module}.{a.name}" for a in node.names]
        else:
            continue
        found += [f"{path.name}:{node.lineno} {m}" for m in mods
                  if m in _FORBIDDEN or m == "backend.models.Reservation"]
    return found


_AGENT_FILES = sorted(p.name for p in AGENT.glob("*.py") if p.name not in _EXEMPT)


def test_the_scan_covers_the_agent_package():
    assert {"tools.py", "core.py", "recording.py"} <= set(_AGENT_FILES)


@pytest.mark.parametrize("name", _AGENT_FILES)
def test_agent_does_not_reach_past_the_provider(name):
    assert _violations(AGENT / name) == []


def test_the_rule_catches_each_import_form(tmp_path):
    bad = tmp_path / "bad.py"
    bad.write_text("from backend.reservations import booking\n"
                   "import backend.reservations.availability\n"
                   "from backend.reservations.models import Reservation\n"
                   "from backend.models import Reservation\n"
                   "from .. import booking\n"
                   "from ..availability import find_slots\n"
                   "from ..models import Reservation\n")
    assert len(_violations(bad)) == 7
    ok = tmp_path / "ok.py"
    ok.write_text("from .models import AgentConversation\n"
                  "from backend.models import Location\n")
    assert _violations(ok) == []


def test_provider_errors_keep_their_codes():
    assert issubclass(SlotUnavailable, ProviderError)
    assert issubclass(Transient, ProviderError)
    assert NotFound.code == "not_found"
    with pytest.raises(ValueError):
        provider_named("nope")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_a_conversations_provider_is_fixed_at_creation(db_session, seeded_floor):
    first = await recording.start_conversation(db_session, COMPANY_ID, LOCATION_ID, "web")
    assert first.provider == "wizscheduler"
    assert isinstance(provider_for(first), WizSchedulerProvider)

    rules, _ = await get_rules(db_session, COMPANY_ID, LOCATION_ID)
    await save_rules(db_session, COMPANY_ID, LOCATION_ID,
                     rules.model_copy(update={"provider": "mock_external"}))
    second = await recording.start_conversation(db_session, COMPANY_ID, LOCATION_ID,
                                                "web")
    await db_session.flush()
    await db_session.refresh(first)
    await db_session.refresh(second)
    assert second.provider == "mock_external"
    assert first.provider == "wizscheduler"  # switching never moves a conversation


@pytest.mark.integration
@pytest.mark.asyncio
async def test_the_wizscheduler_adapter_returns_provider_values(db_session, web_conv):
    now = datetime.now(timezone.utc)
    rules, _ = await get_rules(db_session, COMPANY_ID, LOCATION_ID)
    ctx = ProviderContext(COMPANY_ID, LOCATION_ID, "America/New_York", rules, now)
    provider = provider_named("wizscheduler")
    day = now.astimezone(NY).date() + timedelta(days=7)
    start = datetime.combine(day, time(19), tzinfo=NY)

    slots = await provider.find_slots(db_session, ctx, 2, start,
                                      start + timedelta(minutes=1))
    assert slots and all(isinstance(s, ProviderSlot) for s in slots)
    assert slots[0].start == start and slots[0].party_size == 2

    held = await provider.hold(db_session, ctx, 2, start, name="Ada", phone=PHONE,
                               channel="web", conversation_id=web_conv.id)
    assert isinstance(held, ProviderBooking)
    assert held.status == HELD and held.code is None and not held.external
    assert held.held_until is not None

    booked = await provider.book(db_session, ctx, held.ref)
    assert booked.ref == held.ref and booked.code is not None
    found = await provider.lookup(db_session, ctx, code=booked.code.lower(), phone=PHONE)
    assert found == booked
    assert await provider.lookup(db_session, ctx, ref=held.ref) == booked

    with pytest.raises(ProviderError):
        await provider.hold(db_session, ctx, 99, start, name="Bo", phone=PHONE,
                            channel="web", conversation_id=web_conv.id)
    cancelled = await provider.cancel(db_session, ctx, held.ref)
    assert cancelled.status == "cancelled"
