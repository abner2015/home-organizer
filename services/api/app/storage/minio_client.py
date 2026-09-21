"""Thin boto3 wrapper for MinIO/S3 object storage.

All credentials are read from `app.core.config.settings`. The client is
lazy-instantiated and cached per process. The wrapper exposes only the
operations we need: put, presigned get, delete, ensure bucket.

Security properties:
- `endpoint_url` points to the internal MinIO host; the wrapper never returns
  this URL to API consumers. All access URLs are presigned.
- `aws_access_key_id` / `aws_secret_access_key` come from settings; they are
  never serialized into API responses.
- Public buckets are an explicit anti-pattern: this client does NOT generate
  permanent URLs.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Any

import boto3
from botocore.client import Config as BotoConfig
from botocore.exceptions import ClientError

from app.core.config import settings
from app.core.logging import get_logger
from app.storage.errors import StorageError

logger = get_logger(__name__)

__all__ = [
    "StorageError",
    "delete_object",
    "ensure_bucket",
    "get_object",
    "get_s3_client",
    "object_exists",
    "presigned_get_url",
    "presigned_put_url",
    "put_object",
]


@lru_cache
def get_s3_client() -> Any:
    """Return a process-wide boto3 S3 client configured for MinIO.

    Uses Signature V4 (MinIO default). Connection pooling is handled by
    botocore.
    """
    return boto3.client(
        "s3",
        endpoint_url=_endpoint_url(),
        aws_access_key_id=settings.minio_root_user,
        aws_secret_access_key=settings.minio_root_password,
        config=BotoConfig(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            retries={"max_attempts": 3, "mode": "standard"},
        ),
        region_name="us-east-1",  # MinIO ignores; required by boto3
    )


def _endpoint_url() -> str:
    scheme = "https" if settings.minio_use_ssl else "http"
    return f"{scheme}://{settings.minio_endpoint}"


def ensure_bucket(bucket: str) -> None:
    """Create the bucket if it does not exist. Idempotent."""
    client = get_s3_client()
    try:
        client.head_bucket(Bucket=bucket)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code")
        if code in {"404", "NoSuchBucket", "NotFound"}:
            client.create_bucket(Bucket=bucket)
            logger.info("storage.bucket_created", bucket=bucket)
        else:
            raise StorageError(f"Failed to check bucket {bucket!r}: {exc}") from exc


def put_object(
    bucket: str,
    key: str,
    body: bytes,
    content_type: str,
) -> None:
    """Upload bytes to (bucket, key). Overwrites if exists.

    `body` is bytes (we do not stream UploadFile here — service layer reads
    the file into memory after validation; uploads are bounded by max size).
    """
    try:
        get_s3_client().put_object(
            Bucket=bucket,
            Key=key,
            Body=body,
            ContentType=content_type,
            ServerSideEncryption="AES256",
        )
        logger.info("storage.object_put", bucket=bucket, key=key, size=len(body))
    except ClientError as exc:
        raise StorageError(f"Put failed for {bucket}/{key}: {exc}") from exc


def get_object(bucket: str, key: str) -> bytes:
    """Fetch an object's bytes.

    Used by the vision path, which inlines the image as a `data:` URI rather
    than handing the model a URL it would have to fetch itself.
    """
    try:
        response = get_s3_client().get_object(Bucket=bucket, Key=key)
        body: bytes = response["Body"].read()
        return body
    except ClientError as exc:
        raise StorageError(f"Get failed for {bucket}/{key}: {exc}") from exc


def delete_object(bucket: str, key: str) -> None:
    """Remove an object. Missing objects are not an error."""
    try:
        get_s3_client().delete_object(Bucket=bucket, Key=key)
        logger.info("storage.object_deleted", bucket=bucket, key=key)
    except ClientError as exc:
        raise StorageError(f"Delete failed for {bucket}/{key}: {exc}") from exc


def _rewrite_public_host(url: str) -> str:
    """Rewrite an internal endpoint host to `minio_public_endpoint`.

    Only the host is swapped, so a presigned URL stays valid for a browser that
    can resolve the public name but not the internal one (the signature covers
    the *public* host when MinIO is itself reachable under that name).
    """
    public = settings.minio_public_endpoint
    internal = settings.minio_endpoint
    if public and internal and internal in url:
        return url.replace(internal, public, 1)
    return url


def presigned_get_url(bucket: str, key: str, expires_seconds: int = 3600) -> str:
    """Return a presigned GET URL with the given TTL.

    Frontend uses this to display the image without ever holding credentials.
    If `minio_public_endpoint` is configured, the returned URL's host is
    rewritten so the browser can reach the object over the public network
    (rather than the internal one the API uses to talk to MinIO).
    """
    try:
        url: str = get_s3_client().generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=expires_seconds,
        )
    except ClientError as exc:
        raise StorageError(f"Presign failed for {bucket}/{key}: {exc}") from exc

    return _rewrite_public_host(url)


def presigned_put_url(
    bucket: str,
    key: str,
    content_type: str,
    expires_seconds: int = 600,
) -> str:
    """Return a presigned PUT URL so a browser can upload bytes directly.

    `content_type` is part of the signature, so the client MUST send exactly
    this Content-Type header on the PUT or MinIO answers 403
    SignatureDoesNotMatch.

    This is a purely local signing operation — it does not contact MinIO and
    does not create the bucket. Buckets are provisioned once by the
    `minio-init` compose service (see `docker-compose.yml`).
    """
    try:
        url: str = get_s3_client().generate_presigned_url(
            "put_object",
            Params={"Bucket": bucket, "Key": key, "ContentType": content_type},
            ExpiresIn=expires_seconds,
        )
    except ClientError as exc:
        raise StorageError(f"Presign PUT failed for {bucket}/{key}: {exc}") from exc

    return _rewrite_public_host(url)


def object_exists(bucket: str, key: str) -> bool:
    """Return True iff the object exists at (bucket, key)."""
    try:
        get_s3_client().head_object(Bucket=bucket, Key=key)
        return True
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code")
        if code in {"404", "NoSuchKey", "NotFound"}:
            return False
        raise StorageError(f"Head failed for {bucket}/{key}: {exc}") from exc
