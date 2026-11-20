"""quality.landed_counts on a readable file of the wrong size (#259 step 10)."""

from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from backend.analytics import lake, quality, schemas

DAY = date(2026, 11, 18)


def _usage(i: int) -> dict:
    ts = datetime(2026, 11, 18, 12, i, tzinfo=timezone.utc)
    return {"usage_id": f"u{i}", "company_id": "c1", "ownership_group_id": "og1",
            "location_id": "l1", "conversation_id": None, "product": "reservation_agent",
            "cost_usd": Decimal("0.012345"), "billed_usd": Decimal("1.50"),
            "period_start": ts, "created_at": ts}


def test_a_readable_file_with_the_wrong_row_count_fails(tmp_path: Path) -> None:
    extracted = [_usage(1), _usage(2), _usage(3)]
    # A valid Parquet file that holds only two of the three rows.
    landed = lake.write_partition("agent_usage", DAY, extracted[:2], schemas.AGENT_USAGE,
                                  sink=lake.LocalSink(tmp_path))
    assert landed == 2
    failures = quality.landed_counts({"agent_usage": extracted}, {"agent_usage": landed})
    assert failures == ["landed_count: agent_usage landed 2 of 3 extracted rows"]


def test_matching_counts_pass(tmp_path: Path) -> None:
    rows = [_usage(1)]
    landed = lake.write_partition("agent_usage", DAY, rows, schemas.AGENT_USAGE,
                                  sink=lake.LocalSink(tmp_path))
    assert quality.landed_counts({"agent_usage": rows}, {"agent_usage": landed}) == []
