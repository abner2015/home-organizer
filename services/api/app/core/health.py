"""Health check utilities for /health endpoint."""

from sqlalchemy import text

from app.cache.redis_client import get_redis
from app.core.logging import get_logger
from app.db.session import async_session_factory

logger = get_logger(__name__)


async def check_database() -> tuple[bool, str | None]:
    """Test database connectivity via SELECT 1.

    Returns (ok, error_type). Never raises.
    """
    try:
        async with async_session_factory() as session:
            await session.execute(text("SELECT 1"))
        return True, None
    except Exception as e:
        logger.warning("health.db_failed", error=str(e))
        return False, type(e).__name__


async def check_redis() -> tuple[bool, str | None]:
    """Test Redis connectivity via PING. Never raises."""
    try:
        redis = get_redis()
        await redis.ping()
        return True, None
    except Exception as e:
        logger.warning("health.redis_failed", error=str(e))
        return False, type(e).__name__
