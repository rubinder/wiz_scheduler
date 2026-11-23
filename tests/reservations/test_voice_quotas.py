"""The Service Quotas / CloudWatch edge (decision 103): AWS shapes are
translated into AppliedQuota and plain statuses; nothing here reaches AWS."""

from datetime import date, datetime, timezone

import pytest

from backend.config import settings
from backend.services import voice_quotas
from backend.services.voice_quotas import AppliedQuota, AwsQuotaClient

pytestmark = pytest.mark.asyncio

INSTANCE_ID = "inst-123"


class FakeQuotas:
    def __init__(self, pages, history=None):
        self.pages = pages
        self.history = history or []
        self.calls: list[tuple[str, dict]] = []

    def list_service_quotas(self, **kw):
        self.calls.append(("list", kw))
        i = int(kw.get("NextToken") or 0)
        page = {"Quotas": self.pages[i]}
        if i + 1 < len(self.pages):
            page["NextToken"] = str(i + 1)
        return page

    def request_service_quota_increase(self, **kw):
        self.calls.append(("request", kw))
        return {"RequestedQuota": {"Id": "req-1", "Status": "PENDING"}}

    def list_requested_service_quota_change_history_by_quota(self, **kw):
        """One request per page, to exercise pagination."""
        self.calls.append(("history", kw))
        i = int(kw.get("NextToken") or 0)
        page = {"RequestedQuotas": self.history[i:i + 1]}
        if i + 1 < len(self.history):
            page["NextToken"] = str(i + 1)
        return page


class FakeCloudWatch:
    def __init__(self, points):
        self.points = points
        self.calls: list[dict] = []

    def get_metric_statistics(self, **kw):
        self.calls.append(kw)
        return {"Datapoints": [{"Maximum": p} for p in self.points]}


@pytest.fixture(autouse=True)
def _instance(monkeypatch):
    monkeypatch.setattr(settings, "CONNECT_INSTANCE_ARN",
                        f"arn:aws:connect:us-east-1:1:instance/{INSTANCE_ID}")


def _quota(name, code, value, context=None):
    q = {"QuotaName": name, "QuotaCode": code, "Value": value, "ServiceCode": "connect"}
    if context:
        q["QuotaContext"] = {"ContextScope": "RESOURCE", "ContextId": context}
    return q


async def test_quota_is_found_by_name_across_pages_never_by_code():
    sq = FakeQuotas([[_quota("Phone numbers per instance", "L-1", 5.0)],
                     [_quota(settings.VOICE_QUOTA_NAME, "L-XYZ", 10.0)]])
    client = AwsQuotaClient(sq, FakeCloudWatch([]))
    q = await client.applied_quota()
    assert q == AppliedQuota(code="L-XYZ", value=10, context_id=None)
    assert all(kw["ServiceCode"] == "connect" for name, kw in sq.calls)


async def test_the_instance_entry_wins_over_the_account_default():
    other = f"arn:aws:connect:us-east-1:1:instance/other"
    mine = f"arn:aws:connect:us-east-1:1:instance/{INSTANCE_ID}"
    sq = FakeQuotas([[_quota(settings.VOICE_QUOTA_NAME, "L-XYZ", 10.0),
                      _quota(settings.VOICE_QUOTA_NAME, "L-XYZ", 40.0, other),
                      _quota(settings.VOICE_QUOTA_NAME, "L-XYZ", 25.0, mine)]])
    q = await AwsQuotaClient(sq, FakeCloudWatch([])).applied_quota()
    assert q == AppliedQuota(code="L-XYZ", value=25, context_id=mine)


async def test_missing_quota_is_none():
    sq = FakeQuotas([[_quota("Something else", "L-1", 5.0)]])
    assert await AwsQuotaClient(sq, FakeCloudWatch([])).applied_quota() is None


async def test_request_increase_sends_code_desired_and_context():
    sq = FakeQuotas([[]])
    client = AwsQuotaClient(sq, FakeCloudWatch([]))
    rid = await client.request_increase(AppliedQuota("L-XYZ", 10, "ctx"), 30)
    assert rid == "req-1"
    name, kw = sq.calls[-1]
    assert name == "request"
    assert kw == {"ServiceCode": "connect", "QuotaCode": "L-XYZ",
                  "DesiredValue": 30.0, "ContextId": "ctx"}
    await client.request_increase(AppliedQuota("L-XYZ", 10, None), 30)
    assert "ContextId" not in sq.calls[-1][1]


@pytest.mark.parametrize("aws, ours", [
    ("PENDING", "pending"), ("CASE_OPENED", "pending"), ("APPROVED", "approved"),
    ("DENIED", "denied"), ("CASE_CLOSED", "denied"), ("NOT_APPROVED", "denied"),
    ("INVALID_REQUEST", "denied"),
])
async def test_request_status_is_read_from_the_quota_history(aws, ours):
    sq = FakeQuotas([[]], history=[{"Id": "other", "Status": "APPROVED"},
                                   {"Id": "req-1", "Status": aws}])
    client = AwsQuotaClient(sq, FakeCloudWatch([]))
    assert await client.request_status(AppliedQuota("L-XYZ", 10, None), "req-1") == ours
    assert sq.calls[0] == ("history", {"ServiceCode": "connect", "QuotaCode": "L-XYZ"})
    assert sq.calls[-1] == ("history", {"ServiceCode": "connect", "QuotaCode": "L-XYZ",
                                        "NextToken": "1"})


async def test_unknown_request_id_is_missing_after_every_page():
    sq = FakeQuotas([[]], history=[{"Id": "a", "Status": "PENDING"},
                                   {"Id": "b", "Status": "PENDING"}])
    assert await AwsQuotaClient(sq, FakeCloudWatch([])).request_status(
        AppliedQuota("L-XYZ", 10, None), "gone") == "missing"
    assert len(sq.calls) == 2


async def test_cloudwatch_peak_is_the_day_maximum():
    cw = FakeCloudWatch([3.0, 7.0])
    assert await AwsQuotaClient(FakeQuotas([[]]), cw).peak_concurrent_calls(
        date(2026, 11, 20)) == 7
    kw = cw.calls[0]
    assert kw["Namespace"] == "AWS/Connect" and kw["MetricName"] == "ConcurrentCalls"
    assert {"Name": "InstanceId", "Value": INSTANCE_ID} in kw["Dimensions"]
    assert kw["StartTime"] == datetime(2026, 11, 20, tzinfo=timezone.utc)
    assert kw["EndTime"] == datetime(2026, 11, 21, tzinfo=timezone.utc)
    assert kw["Statistics"] == ["Maximum"]


async def test_cloudwatch_without_data_is_none():
    assert await AwsQuotaClient(FakeQuotas([[]]), FakeCloudWatch([])).peak_concurrent_calls(
        date(2026, 11, 20)) is None


async def test_the_client_can_be_swapped():
    fake = object()
    voice_quotas.set_quota_client(fake)
    assert voice_quotas.get_quota_client() is fake
    voice_quotas.set_quota_client(None)
    assert isinstance(voice_quotas.get_quota_client(), AwsQuotaClient)
