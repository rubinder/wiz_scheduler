"""lake.write_partition and the schemas (#259 step 10)."""

from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from backend.analytics import lake, schemas

DAY = date(2026, 11, 18)


def _usage(i: int) -> dict:
    ts = datetime(2026, 11, 18, 12, i, tzinfo=timezone.utc)
    return {"usage_id": f"u{i}", "company_id": "c1", "ownership_group_id": "og1",
            "location_id": "l1", "conversation_id": None, "product": "reservation_agent",
            "cost_usd": Decimal("0.012345"), "billed_usd": Decimal("1.50"),
            "period_start": ts, "created_at": ts}


def test_writes_the_partition_path(tmp_path: Path) -> None:
    n = lake.write_partition("agent_usage", DAY, [_usage(1)], schemas.AGENT_USAGE,
                             sink=lake.LocalSink(tmp_path))
    assert n == 1
    path = tmp_path / "agent_usage" / "dt=2026-11-18" / "part-0.parquet"
    table = pq.read_table(path)
    assert table.schema.equals(schemas.AGENT_USAGE)
    row = table.to_pylist()[0]
    assert row["billed_usd"] == Decimal("1.500000")
    assert row["created_at"] == datetime(2026, 11, 18, 12, 1, tzinfo=timezone.utc)


def test_rewriting_a_partition_replaces_it(tmp_path: Path) -> None:
    sink = lake.LocalSink(tmp_path)
    lake.write_partition("agent_usage", DAY, [_usage(1), _usage(2)], schemas.AGENT_USAGE, sink=sink)
    stray = tmp_path / "agent_usage" / "dt=2026-11-18" / "part-1.parquet"
    stray.write_bytes(b"old")
    lake.write_partition("agent_usage", DAY, [_usage(3)], schemas.AGENT_USAGE, sink=sink)
    directory = tmp_path / "agent_usage" / "dt=2026-11-18"
    assert [p.name for p in directory.iterdir()] == ["part-0.parquet"]
    assert [r["usage_id"] for r in pq.read_table(directory).to_pylist()] == ["u3"]


def test_an_empty_day_writes_an_empty_file(tmp_path: Path) -> None:
    sink = lake.LocalSink(tmp_path)
    lake.write_partition("agent_usage", DAY, [_usage(1)], schemas.AGENT_USAGE, sink=sink)
    assert lake.write_partition("agent_usage", DAY, [], schemas.AGENT_USAGE, sink=sink) == 0
    table = pq.read_table(tmp_path / "agent_usage" / "dt=2026-11-18" / "part-0.parquet")
    assert table.num_rows == 0 and table.schema.equals(schemas.AGENT_USAGE)


def test_unknown_columns_are_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="diner_name"):
        lake.write_partition("agent_usage", DAY, [{**_usage(1), "diner_name": "x"}],
                             schemas.AGENT_USAGE, sink=lake.LocalSink(tmp_path))


class FakeS3:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}
        self.page_size = 2
        self.deleted: list[str] = []
        self.fail_keys: set[str] = set()

    def list_objects_v2(self, Bucket: str, Prefix: str, ContinuationToken: str | None = None):
        keys = sorted(k for b, k in self.objects if b == Bucket and k.startswith(Prefix))
        start = int(ContinuationToken or 0)
        page = keys[start:start + self.page_size]
        more = start + self.page_size < len(keys)
        out = {"Contents": [{"Key": k} for k in page], "IsTruncated": more}
        if more:
            out["NextContinuationToken"] = str(start + self.page_size)
        return out

    def delete_objects(self, Bucket: str, Delete: dict) -> dict:
        errors = []
        for o in Delete["Objects"]:
            self.deleted.append(o["Key"])
            if o["Key"] in self.fail_keys:
                errors.append({"Key": o["Key"], "Code": "AccessDenied", "Message": "no"})
            else:
                self.objects.pop((Bucket, o["Key"]), None)
        return {"Errors": errors} if errors else {}

    def put_object(self, Bucket: str, Key: str, Body: bytes) -> None:
        self.objects[(Bucket, Key)] = Body


def test_s3_sink_deletes_the_partition_then_puts() -> None:
    s3 = FakeS3()
    s3.objects[("lake", "agent/agent_usage/dt=2026-11-18/part-0.parquet")] = b"old"
    for k in ("a", "b", "c"):  # more than one list page
        s3.objects[("lake", f"agent/agent_usage/dt=2026-11-18/{k}.parquet")] = b"old"
    s3.objects[("lake", "agent/agent_usage/dt=2026-11-17/part-0.parquet")] = b"keep"
    s3.objects[("lake", "agent/agent_usage/dt=2026-11-180/x")] = b"keep"
    lake.write_partition("agent_usage", DAY, [_usage(1)], schemas.AGENT_USAGE,
                         sink=lake.S3Sink("lake", "agent/", s3))
    assert sorted(k for _, k in s3.objects) == [
        "agent/agent_usage/dt=2026-11-17/part-0.parquet",
        "agent/agent_usage/dt=2026-11-18/part-0.parquet",
        "agent/agent_usage/dt=2026-11-180/x",
    ]
    body = s3.objects[("lake", "agent/agent_usage/dt=2026-11-18/part-0.parquet")]
    assert pq.read_table(pa.BufferReader(body)).num_rows == 1
    # The key being written is overwritten in place, never deleted.
    assert "agent/agent_usage/dt=2026-11-18/part-0.parquet" not in s3.deleted


def test_s3_sink_raises_when_a_delete_fails() -> None:
    s3 = FakeS3()
    stale = "agent/agent_usage/dt=2026-11-18/part-1.parquet"
    s3.objects[("lake", stale)] = b"old"
    s3.fail_keys.add(stale)
    with pytest.raises(lake.S3DeleteError, match="AccessDenied"):
        lake.write_partition("agent_usage", DAY, [_usage(1)], schemas.AGENT_USAGE,
                             sink=lake.S3Sink("lake", "agent", s3))


# Frozen column lists: a change here is a lake schema change, which the Glue
# tables (generated from schemas.py) and every query over them must follow.
EXPECTED = {
    "conversations": "conversation_id:string company_id:string location_id:string "
        "channel:string provider:string status:string outcome:string reservation_id:string "
        "turn_count:int32 failed_lookups:int32 cost_usd:decimal128(12, 6) "
        "billed_usd:decimal128(12, 6) started_at:timestamp[us, tz=UTC] "
        "ended_at:timestamp[us, tz=UTC]",
    "events": "event_id:string conversation_id:string company_id:string location_id:string "
        "seq:int32 type:string text_chars:int32 payload:string llm_latency_ms:int64 "
        "tools_latency_ms:int64 input_tokens:int32 output_tokens:int32 "
        "cache_read_tokens:int32 cache_write_tokens:int32 cost_usd:decimal128(12, 6) "
        "created_at:timestamp[us, tz=UTC]",
    "reservations": "reservation_id:string provider:string company_id:string "
        "location_id:string party_size:int32 start_at:timestamp[us, tz=UTC] "
        "end_at:timestamp[us, tz=UTC] status:string source:string conversation_id:string "
        "created_at:timestamp[us, tz=UTC] updated_at:timestamp[us, tz=UTC]",
    "agent_usage": "usage_id:string company_id:string ownership_group_id:string "
        "location_id:string conversation_id:string product:string cost_usd:decimal128(12, 6) "
        "billed_usd:decimal128(12, 6) period_start:timestamp[us, tz=UTC] "
        "created_at:timestamp[us, tz=UTC]",
    "screened_calls": "screened_call_id:string company_id:string location_id:string "
        "decision:string reason:string created_at:timestamp[us, tz=UTC]",
    "eval_results": "run_id:string run_created_at:timestamp[us, tz=UTC] git_sha:string "
        "agent_model:string mode:string recognizer:string prompt_version:string "
        "scenario_id:string attempt:int32 passed:bool failures:string failure_count:int32 "
        "metrics:string",
}


def test_schemas_are_stable() -> None:
    assert set(schemas.TABLES) == set(EXPECTED)
    for name, schema in schemas.TABLES.items():
        got = " ".join(f"{f.name}:{f.type}" for f in schema)
        assert got == EXPECTED[name], name


def test_no_schema_has_an_identifier_column() -> None:
    for schema in schemas.TABLES.values():
        for f in schema:
            assert not any(w in f.name for w in ("name", "email", "phone", "number", "notes",
                                                 "_key", "code"))
            assert f.name != "text"
