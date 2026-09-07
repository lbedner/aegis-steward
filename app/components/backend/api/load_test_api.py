"""HTTP surface for HTTP load-test result storage.

Reads the recent-runs index and per-test detail produced by
``APILoadTestService``. Auth-gated when the auth service is present
(same pattern as ``/api/v1/metrics/*``); open in auth-less projects.

When Redis is not configured (e.g. base stacks without the ``redis``
component), the endpoints return empty / 404 rather than erroring — the
dashboard tab degrades to an empty-state placeholder.
"""

from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from fastapi import APIRouter, HTTPException, Query, status

from app.services.load_test.api.models import APILoadTestResult
from app.services.load_test.common.storage import RedisResultStore

router = APIRouter(prefix="/load-tests/api", tags=["load-tests"])


T = TypeVar("T")


async def _with_store(
    op: Callable[[RedisResultStore[APILoadTestResult] | None], Awaitable[T]],
) -> T:
    """Build a store, run ``op``, close the redis client cleanly.

    Mirrors the lifecycle helper used by the CLI so the redis client is
    always closed within the active event loop (no ``Event loop is closed``
    tracebacks at process shutdown).
    """
    try:
        import redis.asyncio as aioredis

        from app.core.config import settings

        redis_url = getattr(settings, "redis_url_effective", None)
        if not redis_url:
            return await op(None)
        client = aioredis.from_url(redis_url)
    except Exception:
        return await op(None)

    store = RedisResultStore(
        redis=client,
        key_prefix="api_load_test",
        result_model=APILoadTestResult,
    )
    try:
        return await op(store)
    finally:
        await store.aclose()


@router.get("/recent")
async def list_recent_runs(
    limit: int = Query(20, ge=1, le=100, description="Max runs to return"),
) -> list[dict[str, Any]]:
    """Recent HTTP load-test runs, newest first."""

    async def _op(
        store: RedisResultStore[APILoadTestResult] | None,
    ) -> list[APILoadTestResult]:
        if store is None:
            return []
        return await store.list_recent(limit)

    items = await _with_store(_op)
    return [r.model_dump() for r in items]


@router.get("/{test_id}")
async def get_run(
    test_id: str,
) -> dict[str, Any]:
    """Fetch a single HTTP load-test result by ID."""

    async def _op(
        store: RedisResultStore[APILoadTestResult] | None,
    ) -> APILoadTestResult | None:
        if store is None:
            return None
        return await store.get(test_id)

    result = await _with_store(_op)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Result not found"
        )
    return result.model_dump()
