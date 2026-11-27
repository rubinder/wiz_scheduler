"""The Amazon Connect concurrent-calls quota and CloudWatch's call peak
(decision 103): the anti-corruption layer between Service Quotas /
CloudWatch and reservations.voice_capacity.

A deliberately small interface (`QuotaClient`), so the capacity rules never
see boto3 or an AWS field name and tests swap in a fake with
`set_quota_client` (no moto, no new dependencies). The quota is found by its
name (settings.VOICE_QUOTA_NAME), never a hard-coded code. AWS quota changes
cost nothing and only ever go up; the AWS Support API (paid plan) is never
called. boto3 is imported lazily and each call runs in a worker thread;
region and credentials come from the task environment, as archive_storage's
S3 client. Nothing here is called against AWS in CI.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Literal, Protocol

from backend.config import settings

SERVICE_CODE = "connect"
# "missing": AWS has no request with that id for the quota.
RequestStatus = Literal["pending", "approved", "denied", "missing"]

_STATUSES: dict[str, RequestStatus] = {
    "PENDING": "pending", "CASE_OPENED": "pending",
    "APPROVED": "approved",
    "DENIED": "denied", "CASE_CLOSED": "denied", "NOT_APPROVED": "denied",
    "INVALID_REQUEST": "denied",
}


class QuotaRequestAlreadyOpen(Exception):
    """AWS already holds an open increase request for this quota (it takes
    one at a time): the raise is still pending upstream."""


@dataclass(frozen=True)
class AppliedQuota:
    """The quota as it applies to our instance: its code (for requests), the
    applied value, and the resource context when the quota is per resource."""
    code: str
    value: int
    context_id: str | None = None


class QuotaClient(Protocol):
    async def applied_quota(self) -> AppliedQuota | None: ...

    async def request_increase(self, quota: AppliedQuota, desired: int) -> str:
        """The new request's id. QuotaRequestAlreadyOpen when AWS already
        holds an open request for the quota."""
        ...

    async def request_status(self, quota: AppliedQuota, request_id: str) -> RequestStatus: ...

    async def peak_concurrent_calls(self, day: date) -> int | None: ...


def _instance_id() -> str:
    return settings.CONNECT_INSTANCE_ARN.rsplit("/", 1)[-1]


class AwsQuotaClient:
    def __init__(self, servicequotas: Any = None, cloudwatch: Any = None) -> None:
        self._sq = servicequotas
        self._cw = cloudwatch

    def _quotas(self) -> Any:
        if self._sq is None:
            import boto3

            self._sq = boto3.client("service-quotas")
        return self._sq

    def _cloudwatch(self) -> Any:
        if self._cw is None:
            import boto3

            self._cw = boto3.client("cloudwatch")
        return self._cw

    def _find(self) -> AppliedQuota | None:
        matches: list[dict[str, Any]] = []
        token: str | None = None
        while True:
            kw: dict[str, Any] = {"ServiceCode": SERVICE_CODE, "QuotaAppliedAtLevel": "ALL"}
            if token:
                kw["NextToken"] = token
            page = self._quotas().list_service_quotas(**kw)
            matches += [q for q in page.get("Quotas") or []
                        if q.get("QuotaName") == settings.VOICE_QUOTA_NAME]
            token = page.get("NextToken")
            if not token:
                break
        if not matches:
            return None
        instance = _instance_id()

        def context(q: dict[str, Any]) -> str | None:
            return (q.get("QuotaContext") or {}).get("ContextId")

        mine = [q for q in matches if (context(q) or "").rsplit("/", 1)[-1] == instance
                and context(q)]
        account = [q for q in matches if not context(q)]
        chosen = (mine or account or matches)[0]
        return AppliedQuota(code=chosen["QuotaCode"], value=int(chosen.get("Value") or 0),
                            context_id=context(chosen))

    async def applied_quota(self) -> AppliedQuota | None:
        return await asyncio.to_thread(self._find)

    async def request_increase(self, quota: AppliedQuota, desired: int) -> str:
        kw: dict[str, Any] = {"ServiceCode": SERVICE_CODE, "QuotaCode": quota.code,
                              "DesiredValue": float(desired)}
        if quota.context_id:
            kw["ContextId"] = quota.context_id
        try:
            out = await asyncio.to_thread(self._quotas().request_service_quota_increase, **kw)
        except Exception as exc:
            code = (getattr(exc, "response", None) or {}).get("Error", {}).get("Code")
            if code == "ResourceAlreadyExistsException":
                raise QuotaRequestAlreadyOpen() from exc
            raise
        return str((out.get("RequestedQuota") or {}).get("Id") or "")

    async def request_status(self, quota: AppliedQuota, request_id: str) -> RequestStatus:
        token: str | None = None
        while True:
            kw: dict[str, Any] = {"ServiceCode": SERVICE_CODE, "QuotaCode": quota.code}
            if token:
                kw["NextToken"] = token
            out = await asyncio.to_thread(
                self._quotas().list_requested_service_quota_change_history_by_quota, **kw)
            for r in out.get("RequestedQuotas") or []:
                if r.get("Id") == request_id:
                    return _STATUSES.get(str(r.get("Status")), "pending")
            token = out.get("NextToken")
            if not token:
                return "missing"

    async def peak_concurrent_calls(self, day: date) -> int | None:
        start = datetime.combine(day, time(0), tzinfo=timezone.utc)
        out = await asyncio.to_thread(
            self._cloudwatch().get_metric_statistics,
            Namespace="AWS/Connect", MetricName="ConcurrentCalls",
            Dimensions=[{"Name": "InstanceId", "Value": _instance_id()},
                        {"Name": "MetricGroup", "Value": "VoiceCalls"}],
            StartTime=start, EndTime=start + timedelta(days=1),
            Period=86400, Statistics=["Maximum"])
        points = [p.get("Maximum") for p in out.get("Datapoints") or []
                  if p.get("Maximum") is not None]
        return int(max(points)) if points else None


_client: Any = None


def get_quota_client() -> QuotaClient:
    global _client
    if _client is None:
        _client = AwsQuotaClient()
    return _client


def set_quota_client(client: Any) -> None:
    """Swap the client (tests); None restores the AWS one on next use."""
    global _client
    _client = client
