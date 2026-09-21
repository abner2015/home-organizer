"""Storage-layer exception types.

Lives in its own leaf module so both `minio_client` and `backend` can raise the
same class without importing each other. `minio_client` re-exports it, so the
historical `minio_client.StorageError` spelling keeps working.
"""
from __future__ import annotations


class StorageError(Exception):
    """Raised when an object-storage operation fails.

    Wraps backend-specific errors (boto3/botocore, filesystem) so callers don't
    need to know which backend is configured.
    """


__all__ = ["StorageError"]
