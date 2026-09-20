"""Async Redis client wrapper."""
from functools import lru_cache

import redis.asyncio as aioredis

from app.core.config import settings


@lru_cache
def get_redis() -> aioredis.Redis:
    """Return a process-wide Redis client (lazy, cached)."""
    return aioredis.from_url(
        settings.redis_url,
        max_connections=settings.redis_max_connections,
        decode_responses=True,
    )
