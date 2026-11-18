"""Amazon Connect phone numbers for the AI host's voice channel (#259 step 6).

The only module that calls Connect's number APIs. Every call is a no-op that
returns DISABLED unless settings.VOICE_PROVISIONING_ENABLED is true: nothing
billable is claimed by merging or deploying this code. boto3 is blocking, so
each call runs in a worker thread; the client is created lazily (region and
credentials from the task environment, as archive_storage's S3 client).
Tests replace `_make_client`; nothing here is ever called against AWS in CI.
"""

import asyncio
from typing import Any, Literal

from backend.config import settings

DISABLED: Literal["disabled"] = "disabled"

_client_obj: Any = None


class NoNumberAvailable(Exception):
    """Connect offered no number for the country (and prefix)."""


def _make_client() -> Any:
    import boto3

    return boto3.client("connect")


def _client() -> Any:
    global _client_obj
    if _client_obj is None:
        _client_obj = _make_client()
    return _client_obj


def reset_client() -> None:
    """Drop the cached client (tests swap the factory)."""
    global _client_obj
    _client_obj = None


def enabled() -> bool:
    return bool(settings.VOICE_PROVISIONING_ENABLED)


def _instance_id() -> str:
    """The instance id is the ARN's last path segment
    (arn:aws:connect:<region>:<account>:instance/<id>)."""
    return settings.CONNECT_INSTANCE_ARN.rsplit("/", 1)[-1]


def _claim(country: str, prefix: str | None, client_token: str) -> tuple[str, str]:
    c = _client()
    search: dict[str, Any] = {
        "TargetArn": settings.CONNECT_INSTANCE_ARN,
        "PhoneNumberCountryCode": country,
        "PhoneNumberType": "DID",
        "MaxResults": 1,
    }
    if prefix:
        search["PhoneNumberPrefix"] = prefix
    found = c.search_available_phone_numbers(**search).get("AvailableNumbersList") or []
    if not found:
        raise NoNumberAvailable(f"no {country} DID available")
    e164 = found[0]["PhoneNumber"]
    claimed = c.claim_phone_number(
        TargetArn=settings.CONNECT_INSTANCE_ARN, PhoneNumber=e164,
        ClientToken=client_token)
    return e164, claimed["PhoneNumberId"]


async def claim(country: str = "US", prefix: str | None = None, *,
                client_token: str) -> tuple[str, str] | Literal["disabled"]:
    """Search one available DID and claim it: (e164, phone_number_id).
    `client_token` makes a retried claim idempotent; callers derive it from
    the location's voice number row."""
    if not enabled():
        return DISABLED
    return await asyncio.to_thread(_claim, country, prefix, client_token)


async def associate(phone_number_id: str,
                    contact_flow_id: str | None = None) -> None | Literal["disabled"]:
    """Route calls to the number into the AI host's contact flow."""
    if not enabled():
        return DISABLED
    await asyncio.to_thread(
        _client().associate_phone_number_contact_flow,
        PhoneNumberId=phone_number_id, InstanceId=_instance_id(),
        ContactFlowId=contact_flow_id or settings.CONNECT_CONTACT_FLOW_ID)
    return None


async def release(phone_number_id: str) -> None | Literal["disabled"]:
    """Give the number back to Connect (billing for it stops)."""
    if not enabled():
        return DISABLED
    await asyncio.to_thread(_client().release_phone_number, PhoneNumberId=phone_number_id)
    return None
