"""Operator alert on credit charges (#64).

The operator tops up the Anthropic Console by hand; this email tells them
how much. Customers pay LLM_OVERAGE_MARKUP × token cost, so a $10 pack
covers $10 / 1.30 = $7.69 of Anthropic usage.
"""
import logging

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models.ownership_group import OwnershipGroup
from backend.services.operator_alerts import send_credit_purchase_alert
from tests.conftest import _id

pytestmark = pytest.mark.asyncio


@pytest.fixture
def two_groups():
    return [
        OwnershipGroup(id=_id(), name="Alpha Cafés", ai_credits_usd=10.0),
        OwnershipGroup(id=_id(), name="Beta Bars", ai_credits_usd=5.0),
    ]


async def test_alert_logs_and_skips_email_when_unconfigured(
    db_session: AsyncSession, two_groups, monkeypatch, caplog
):
    monkeypatch.setattr(settings, "OPERATOR_ALERT_EMAIL", "")
    db_session.add_all(two_groups)
    await db_session.commit()

    with caplog.at_level(logging.INFO, logger="backend.services.operator_alerts"):
        sent = await send_credit_purchase_alert(db_session, two_groups[0], 10.0, "purchase")

    assert sent is False
    line = next(r.message for r in caplog.records if "[OPERATOR] credit_charge" in r.message)
    assert "kind=purchase" in line
    assert "paid=10.00" in line
    assert "anthropic_equiv=7.69" in line
    assert "total_prepaid=15.00" in line
    assert "total_anthropic_equiv=11.54" in line


async def test_alert_emails_operator_with_amounts(
    db_session: AsyncSession, two_groups, monkeypatch
):
    import resend
    monkeypatch.setattr(settings, "OPERATOR_ALERT_EMAIL", "ops@example.com")
    monkeypatch.setattr(settings, "RESEND_API_KEY", "re_test")
    captured = {}
    monkeypatch.setattr(resend.Emails, "send", lambda params: captured.update(params) or {"id": "em_1"})
    db_session.add_all(two_groups)
    await db_session.commit()

    sent = await send_credit_purchase_alert(db_session, two_groups[0], 10.0, "autoreload")

    assert sent is True
    assert captured["to"] == ["ops@example.com"]
    assert captured["from"] == settings.FROM_EMAIL
    assert captured["subject"] == "[WizScheduler] AI credit auto-reload $10.00 — top up Anthropic by $7.69"
    html = captured["html"]
    assert "Alpha Caf" in html
    assert two_groups[0].id in html
    assert "$7.69" in html
    assert "$15.00" in html and "$11.54" in html


async def test_alert_swallows_resend_failure(
    db_session: AsyncSession, two_groups, monkeypatch
):
    import resend
    monkeypatch.setattr(settings, "OPERATOR_ALERT_EMAIL", "ops@example.com")
    monkeypatch.setattr(settings, "RESEND_API_KEY", "re_test")
    def boom(params):
        raise RuntimeError("resend down")
    monkeypatch.setattr(resend.Emails, "send", boom)
    db_session.add_all(two_groups)
    await db_session.commit()

    sent = await send_credit_purchase_alert(db_session, two_groups[1], 50.0, "purchase")
    assert sent is False
