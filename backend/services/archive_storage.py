"""Where paid payroll archives (#218) are stored.

A deliberately small interface, so the cancellation cron and the download
route never see boto3 and tests can swap in an in-memory fake with
`set_archive_storage` (no moto, no new test dependencies).

The S3 implementation writes to settings.PAYROLL_ARCHIVE_BUCKET. The ECS task
role may put and get `archives/*` objects only -- no delete, no list -- and
the bucket's lifecycle rule expires them after PAYROLL_ARCHIVE_RETENTION_DAYS.
boto3 is imported lazily inside it, so nothing else pays for the import.
"""
from __future__ import annotations

import asyncio
from typing import BinaryIO, Protocol

from backend.config import settings


class ArchiveStorageNotConfigured(RuntimeError):
    """PAYROLL_ARCHIVE_BUCKET is empty: there is nowhere to write."""


class ArchiveStorage(Protocol):
    async def put(self, key: str, fileobj: BinaryIO, size: int) -> None: ...

    def presign_get(self, key: str, expires_seconds: int, download_filename: str) -> str: ...


class S3ArchiveStorage:
    """GLACIER_IR (instant retrieval, so a presigned GET just works),
    SSE-S3, application/zip."""

    def __init__(self, bucket: str) -> None:
        if not bucket:
            raise ArchiveStorageNotConfigured("PAYROLL_ARCHIVE_BUCKET is not set")
        self.bucket = bucket
        self._client_obj = None

    def _client(self):
        if self._client_obj is None:
            import boto3
            from botocore.config import Config

            # SigV4 for presigned URLs; region and credentials come from the
            # task's environment (AWS_REGION, the ECS task role).
            self._client_obj = boto3.client(
                "s3", config=Config(signature_version="s3v4")
            )
        return self._client_obj

    async def put(self, key: str, fileobj: BinaryIO, size: int) -> None:
        fileobj.seek(0)
        # boto3 is blocking; keep the event loop (and the other requests on
        # this worker) free while the file goes up.
        await asyncio.to_thread(
            self._client().put_object,
            Bucket=self.bucket,
            Key=key,
            Body=fileobj,
            ContentLength=size,
            ContentType="application/zip",
            StorageClass="GLACIER_IR",
            ServerSideEncryption="AES256",
        )

    def presign_get(self, key: str, expires_seconds: int, download_filename: str) -> str:
        # Signing is local: no network call, no s3:ListBucket needed.
        return self._client().generate_presigned_url(
            "get_object",
            Params={
                "Bucket": self.bucket,
                "Key": key,
                "ResponseContentDisposition": f'attachment; filename="{download_filename}"',
                "ResponseContentType": "application/zip",
            },
            ExpiresIn=expires_seconds,
        )


_override: ArchiveStorage | None = None
_default: S3ArchiveStorage | None = None


def get_archive_storage() -> ArchiveStorage:
    """The storage in use. Raises ArchiveStorageNotConfigured when no bucket
    is set and no override is installed."""
    global _default
    if _override is not None:
        return _override
    if _default is None or _default.bucket != settings.PAYROLL_ARCHIVE_BUCKET:
        _default = S3ArchiveStorage(settings.PAYROLL_ARCHIVE_BUCKET)
    return _default


def set_archive_storage(storage: ArchiveStorage | None) -> None:
    """For tests: install an in-memory fake, or None to restore S3."""
    global _override
    _override = storage


def archive_key(archive_id: str) -> str:
    return f"archives/{archive_id}.zip"
