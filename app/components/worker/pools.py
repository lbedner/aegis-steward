"""
Worker pool management for client-side task enqueueing.

This module provides Redis connection pooling and caching for enqueueing tasks
to worker queues. Separated from worker management to allow clean architectural
separation between client-side enqueueing and worker-side processing.
"""

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from app.core.config import get_default_queue, settings
from app.core.log import logger

# Global pool cache to avoid creating new Redis connections repeatedly
_pool_cache: dict[str, ArqRedis] = {}


async def get_queue_pool(queue_type: str | None = None) -> tuple[ArqRedis, str]:
    """
    Get Redis pool for enqueuing tasks to specific functional queue.

    Uses connection pooling to avoid creating new Redis connections repeatedly.

    Args:
        queue_type: Functional queue type (defaults to configured default queue)

    Returns:
        Tuple of (pool, queue_name) for enqueueing tasks
    """
    # Use configured default queue if not specified
    if queue_type is None:
        queue_type = get_default_queue()

    from app.core.config import get_available_queues, is_valid_queue

    if not is_valid_queue(queue_type):
        available = get_available_queues()
        raise ValueError(f"Invalid queue type '{queue_type}'. Available: {available}")

    from app.components.worker.registry import get_queue_metadata

    queue_name = get_queue_metadata(queue_type)["queue_name"]

    # Check cache first to avoid creating new Redis connections
    redis_url = (
        settings.redis_url_effective
        if hasattr(settings, "redis_url_effective")
        else settings.REDIS_URL
    )
    cache_key = f"{queue_type}_{redis_url}"

    if cache_key in _pool_cache:
        # Reuse existing pool
        cached_pool = _pool_cache[cache_key]
        try:
            # Test if pool is still valid by doing a quick ping
            await cached_pool.ping()
            return cached_pool, queue_name
        except Exception:
            # Pool is stale, remove from cache and create new one
            logger.debug(f"Removing stale pool from cache: {cache_key}")
            del _pool_cache[cache_key]

    # Create new Redis pool and cache it with improved connection settings
    redis_url = (
        settings.redis_url_effective
        if hasattr(settings, "redis_url_effective")
        else settings.REDIS_URL
    )
    base_settings = RedisSettings.from_dsn(redis_url)
    redis_settings = RedisSettings(
        host=base_settings.host,
        port=base_settings.port,
        database=base_settings.database,
        password=base_settings.password,
        conn_timeout=settings.REDIS_CONN_TIMEOUT,
        conn_retries=settings.REDIS_CONN_RETRIES,
        conn_retry_delay=settings.REDIS_CONN_RETRY_DELAY,
    )
    pool = await create_pool(redis_settings)
    _pool_cache[cache_key] = pool

    logger.debug(f"Created and cached new Redis pool: {cache_key}")
    return pool, queue_name


async def clear_pool_cache() -> None:
    """Clear all cached pools. Use during shutdown or for testing."""
    for cache_key, pool in _pool_cache.items():
        try:
            await pool.aclose()
            logger.debug(f"Closed cached pool: {cache_key}")
        except Exception as e:
            logger.warning(f"Error closing cached pool {cache_key}: {e}")

    _pool_cache.clear()
    logger.info("Pool cache cleared")
