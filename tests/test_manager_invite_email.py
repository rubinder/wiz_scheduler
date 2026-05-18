"""Verify the manager-invite email helper hits Resend with the right payload."""
from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.services.manager_invite_email import send_manager_invite_email


@pytest.mark.asyncio
async def test_send_manager_invite_email_calls_resend(monkeypatch):
    monkeypatch.setattr(settings, "RESEND_API_KEY", "re_test_key")
    monkeypatch.setattr(settings, "FROM_EMAIL", "noreply@wiz.test")

    sent = []

    class FakeEmails:
        @staticmethod
        def send(payload):
            sent.append(payload)

    class FakeResend:
        api_key = None
        Emails = FakeEmails()

    import sys
    monkeypatch.setitem(sys.modules, "resend", FakeResend)

    await send_manager_invite_email(
        email="newmgr@example.com",
        group_name="Acme OG",
        invite_url="https://app.wiz.test/accept-manager-invite?token=abc",
    )
    assert len(sent) == 1
    assert "newmgr@example.com" in sent[0]["to"]
    assert "Acme OG" in sent[0]["html"]
    assert "accept-manager-invite?token=abc" in sent[0]["html"]
    assert "manager" in sent[0]["subject"].lower()


@pytest.mark.asyncio
async def test_send_manager_invite_email_noop_without_resend_key(monkeypatch):
    monkeypatch.setattr(settings, "RESEND_API_KEY", "")
    await send_manager_invite_email(
        email="x@y.test", group_name="Acme", invite_url="https://x.test/y"
    )  # must not raise
