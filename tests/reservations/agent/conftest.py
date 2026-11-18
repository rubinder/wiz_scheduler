"""Agent fixtures. `seeded_floor` comes from tests/reservations/conftest.py
(America/New_York, dinner 17:00-21:00 daily); this adds a web conversation
on that floor."""

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from backend.reservations.agent import recording
from backend.reservations.agent.models import AgentConversation
from tests.conftest import COMPANY_ID, LOCATION_ID


@pytest_asyncio.fixture
async def web_conv(db_session: AsyncSession, seeded_floor) -> AgentConversation:
    return await recording.start_conversation(db_session, COMPANY_ID, LOCATION_ID, "web")
