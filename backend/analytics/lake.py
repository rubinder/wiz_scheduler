"""Land rows as one Parquet file per table per UTC day (#259 step 10):
`<table>/dt=YYYY-MM-DD/part-0.parquet`, replacing whatever that partition
held, so rerunning a day is idempotent."""

import io
import shutil
from datetime import date
from pathlib import Path
from typing import Any, Protocol

import pyarrow as pa
import pyarrow.parquet as pq

PART = "part-0.parquet"


def partition(table: str, day: date) -> str:
    """`<table>/dt=YYYY-MM-DD`, the partition's path under the lake root."""
    return f"{table}/dt={day.isoformat()}"


class Sink(Protocol):
    def replace(self, partition: str, data: bytes) -> str:
        """Delete everything under `partition`, then write `data` as its one
        part file. Returns where it was written."""
        ...


class LocalSink:
    """A directory on disk, for local runs and tests."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def replace(self, partition: str, data: bytes) -> str:
        directory = self.root / partition
        if directory.exists():
            shutil.rmtree(directory)
        directory.mkdir(parents=True)
        path = directory / PART
        path.write_bytes(data)
        return str(path)


class S3DeleteError(RuntimeError):
    """delete_objects reported per-key errors: the partition may still hold
    stale objects next to the new part file."""


class S3Sink:
    """An S3 prefix. `client` is a boto3 S3 client (or anything with its
    list_objects_v2 / delete_objects / put_object)."""

    def __init__(self, bucket: str, prefix: str, client: Any) -> None:
        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self.client = client

    def _key(self, *parts: str) -> str:
        return "/".join(p for p in (self.prefix, *parts) if p)

    def replace(self, partition: str, data: bytes) -> str:
        """Put the part file (S3 overwrites a key atomically, so readers
        never see the partition empty), then delete every other object
        under the partition. Raises S3DeleteError if a delete fails."""
        directory = self._key(partition) + "/"
        key = directory + PART
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data)
        stale: list[str] = []
        token: str | None = None
        while True:
            kwargs: dict[str, Any] = {"Bucket": self.bucket, "Prefix": directory}
            if token:
                kwargs["ContinuationToken"] = token
            page = self.client.list_objects_v2(**kwargs)
            stale += [o["Key"] for o in page.get("Contents", []) if o["Key"] != key]
            if not page.get("IsTruncated"):
                break
            token = page.get("NextContinuationToken")
        for i in range(0, len(stale), 1000):
            resp = self.client.delete_objects(Bucket=self.bucket, Delete={
                "Objects": [{"Key": k} for k in stale[i:i + 1000]], "Quiet": True})
            errors = (resp or {}).get("Errors") or []
            if errors:
                raise S3DeleteError(
                    f"could not delete {len(errors)} object(s) under {directory}: "
                    + ", ".join(f"{e.get('Key')} ({e.get('Code')})" for e in errors[:5]))
        return f"s3://{self.bucket}/{key}"


def to_parquet(rows: list[dict[str, Any]], schema: pa.Schema) -> bytes:
    """`rows` as Parquet bytes under exactly `schema` (extra keys are an
    error, missing nullable ones are NULL)."""
    names = set(schema.names)
    for row in rows:
        extra = set(row) - names
        if extra:
            raise ValueError(f"columns not in the schema: {sorted(extra)}")
    table = pa.Table.from_pylist(rows, schema=schema)
    buf = io.BytesIO()
    pq.write_table(table, buf, compression="snappy")
    return buf.getvalue()


def write_partition(table: str, day: date, rows: list[dict[str, Any]],
                    schema: pa.Schema, *, sink: Sink) -> int:
    """Replace `table`'s partition for `day` with `rows`. An empty day still
    writes a (zero-row) file, so a rerun clears what an earlier one landed.
    Returns the number of rows written."""
    sink.replace(partition(table, day), to_parquet(rows, schema))
    return len(rows)
