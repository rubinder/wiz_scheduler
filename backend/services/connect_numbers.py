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


def _search(country: str, prefix: str | None) -> str:
    search: dict[str, Any] = {
        "TargetArn": settings.CONNECT_INSTANCE_ARN,
        "PhoneNumberCountryCode": country,
        "PhoneNumberType": "DID",
        "MaxResults": 1,
    }
    if prefix:
        search["PhoneNumberPrefix"] = prefix
    found = _client().search_available_phone_numbers(**search).get("AvailableNumbersList") or []
    if not found:
        raise NoNumberAvailable(f"no {country} DID available")
    return found[0]["PhoneNumber"]


def _claim_number(e164: str, client_token: str) -> str:
    claimed = _client().claim_phone_number(
        TargetArn=settings.CONNECT_INSTANCE_ARN, PhoneNumber=e164,
        ClientToken=client_token)
    return claimed["PhoneNumberId"]


async def search(country: str = "US", prefix: str | None = None) -> str | Literal["disabled"]:
    """One available DID (E.164), not yet claimed."""
    if not enabled():
        return DISABLED
    return await asyncio.to_thread(_search, country, prefix)


async def claim_number(e164: str, *, client_token: str) -> str | Literal["disabled"]:
    """Claim a searched number: its phone_number_id. The same e164 and token
    on a retry is idempotent, so a caller that persists both before calling
    never loses a claimed number."""
    if not enabled():
        return DISABLED
    return await asyncio.to_thread(_claim_number, e164, client_token)


async def claim(country: str = "US", prefix: str | None = None, *,
                client_token: str) -> tuple[str, str] | Literal["disabled"]:
    """search + claim_number in one: (e164, phone_number_id). The voice
    number service persists the searched number in between instead."""
    e164 = await search(country, prefix)
    if e164 == DISABLED:
        return DISABLED
    phone_number_id = await claim_number(e164, client_token=client_token)
    return e164, phone_number_id


def _find_owned(e164: str) -> str | None:
    kwargs: dict[str, Any] = {"TargetArn": settings.CONNECT_INSTANCE_ARN,
                              "PhoneNumberPrefix": e164, "MaxResults": 100}
    while True:
        page = _client().list_phone_numbers_v2(**kwargs)
        for summary in page.get("ListPhoneNumbersSummaryList") or []:
            if summary.get("PhoneNumber") == e164:
                return summary["PhoneNumberId"]
        token = page.get("NextToken")
        if not token:
            return None
        kwargs["NextToken"] = token


async def find_owned(e164: str) -> str | None | Literal["disabled"]:
    """The phone_number_id of `e164` if our instance has claimed it, else
    None: how a claim whose answer was lost is told apart from one that
    never happened."""
    if not enabled():
        return DISABLED
    return await asyncio.to_thread(_find_owned, e164)


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
