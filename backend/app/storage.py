"""Object storage for raw uploaded files. Local filesystem by default; S3-compatible when
``TRACEMIND_STORAGE_BACKEND=s3`` (requires ``boto3``, installed separately)."""

from __future__ import annotations

import re
from pathlib import Path

from app.config import get_settings

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def object_key(project_id: str, job_id: str, filename: str) -> str:
    return f"projects/{project_id}/ingestions/{job_id}/{_SAFE.sub('_', filename)[:120]}"


class LocalStorage:
    def __init__(self, root: str) -> None:
        self.root = Path(root).resolve()

    def put(self, key: str, data: bytes) -> str:
        path = (self.root / key).resolve()
        if self.root not in path.parents:
            raise ValueError("invalid storage key")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return key

    def get(self, key: str) -> bytes:
        path = (self.root / key).resolve()
        if self.root not in path.parents:
            raise ValueError("invalid storage key")
        return path.read_bytes()


class S3Storage:  # pragma: no cover - exercised only with real credentials
    def __init__(self, bucket: str, endpoint_url: str | None, region: str | None) -> None:
        import boto3

        self.bucket = bucket
        self.client = boto3.client("s3", endpoint_url=endpoint_url, region_name=region)

    def put(self, key: str, data: bytes) -> str:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data)
        return key

    def get(self, key: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()


def get_storage():
    settings = get_settings()
    if settings.storage_backend == "s3" and settings.s3_bucket:
        return S3Storage(settings.s3_bucket, settings.s3_endpoint_url, settings.s3_region)
    return LocalStorage(settings.storage_local_dir)
