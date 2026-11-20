"""Land rows as one Parquet file per table per UTC day (#259 step 10):
`<table>/dt=YYYY-MM-DD/part-0.parquet`, replacing whatever that partition
held, so rerunning a day is idempotent.

A sink reports the row count of what it actually wrote, read back from the
stored file's Parquet footer, so the landed-count quality check compares
the extract against storage, not against itself. UNREADABLE (-1) means the
stored object could not be parsed (a truncated write)."""

import io
import logging
import shutil
from datetime import date
from pathlib import Path
from typing import Any, Protocol

import pyarrow as pa
import pyarrow.parquet as pq

PART = "part-0.parquet"
UNREADABLE = -1

logger = logging.getLogger(__name__)


def num_rows(source: Any) -> int:
    """Rows in the Parquet file at `source` (a path or a file-like), from its
    footer; UNREADABLE if it does not parse."""
    try:
        return pq.read_metadata(source).num_rows
    except (pa.ArrowException, OSError, ValueError):
        return UNREADABLE


def partition(table: str, day: date) -> str:
    """`<table>/dt=YYYY-MM-DD`, the partition's path under the lake root."""
    return f"{table}/dt={day.isoformat()}"


class Sink(Protocol):
    def replace(self, partition: str, data: bytes) -> int:
        """Delete everything under `partition`, then write `data` as its one
        part file. Returns the row count read back from what was stored
        (UNREADABLE if it does not parse)."""
        ...


class LocalSink:
    """A directory on disk, for local runs and tests."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _write(self, path: Path, data: bytes) -> None:
        path.write_bytes(data)

    def replace(self, partition: str, data: bytes) -> int:
        """Returns the rows in the file's footer, read back from disk."""
        directory = self.root / partition
        if directory.exists():
            shutil.rmtree(directory)
        directory.mkdir(parents=True)
        path = directory / PART
        self._write(path, data)
        return num_rows(path)


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

    def replace(self, partition: str, data: bytes) -> int:
        """Put the part file (S3 overwrites a key atomically, so readers
        never see the partition empty), then delete every other object
        under the partition. Raises S3DeleteError if a delete fails.

        Returns the row count from the footer of the bytes put, once
        head_object confirms the stored object has exactly their size. If it
        does not, the object is fetched back and its own footer read
        (UNREADABLE when it does not parse)."""
        directory = self._key(partition) + "/"
        key = directory + PART
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data)
        stored = self.client.head_object(Bucket=self.bucket, Key=key).get("ContentLength")
        if stored == len(data):
            rows = num_rows(io.BytesIO(data))
        else:
            logger.error("analytics: s3://%s/%s stored %s bytes of %d", self.bucket, key,
                         stored, len(data))
            body = self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()
            rows = num_rows(io.BytesIO(body))
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
        return rows


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
    Returns the row count read back from what the sink stored (UNREADABLE
    if it does not parse), for the landed-count check to compare with
    len(rows)."""
    return sink.replace(partition(table, day), to_parquet(rows, schema))
