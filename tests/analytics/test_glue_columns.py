"""The Glue columns in terraform/analytics_columns.tf.json are generated from
the pyarrow schemas (#259 step 10); a schema change without a regen fails."""

import json
from pathlib import Path

import pyarrow as pa
import pytest

from backend.analytics import glue_columns
from backend.analytics.schemas import TABLES

GENERATED = Path(__file__).resolve().parents[2] / "terraform" / "analytics_columns.tf.json"


def test_checked_in_file_is_current() -> None:
    assert GENERATED.read_text() == glue_columns.render(), (
        "stale: run `python -m backend.analytics.glue_columns > "
        "terraform/analytics_columns.tf.json`")


def test_columns_match_schemas_by_name_and_type() -> None:
    cols = json.loads(GENERATED.read_text())["locals"]["analytics_columns"]
    assert set(cols) == set(TABLES)
    expected = {
        pa.timestamp("us", tz="UTC"): "timestamp",
        pa.decimal128(12, 6): "decimal(12,6)",
        pa.int32(): "int",
        pa.int64(): "bigint",
        pa.string(): "string",
        pa.bool_(): "boolean",
    }
    for table, schema in TABLES.items():
        assert [c["name"] for c in cols[table]] == schema.names
        for field, col in zip(schema, cols[table]):
            assert col["type"] == expected[field.type], (table, field.name)
        assert "dt" not in schema.names  # the partition key, declared in Terraform


@pytest.mark.parametrize(("type_", "glue"), [
    (pa.date32(), "date"),
    (pa.int16(), "smallint"),
    (pa.float64(), "double"),
    (pa.decimal128(10, 2), "decimal(10,2)"),
])
def test_glue_type_mapping(type_: pa.DataType, glue: str) -> None:
    assert glue_columns.glue_type(type_) == glue


def test_unknown_type_raises() -> None:
    with pytest.raises(ValueError):
        glue_columns.glue_type(pa.list_(pa.string()))
