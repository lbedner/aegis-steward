"""Redis-backed result storage shared between load-test variants.

Layout per variant (``key_prefix``):

  ``<prefix>:results:<test_id>``   - JSON blob (one Pydantic ``.model_dump_json()``)
  ``<prefix>:recent``              - sorted set, score = unix timestamp, member = test_id

The ZSET keeps ``list_recent`` cheap (``ZREVRANGE`` + ``MGET``) without
scanning keyspace. Both keys carry the same TTL; the result blob expires
automatically and the index entry is removed on the next ``list_recent``
call or whenever the matching ``MGET`` returns ``None`` for an expired key.

The HTTP load-test service uses this immediately. The worker service can
adopt it later when it outgrows reading arq's own ``arq:result:*`` storage;
the abstraction is variant-agnostic.
"""

import time
from typing import Any, Generic, TypeVar

from app.services.load_test.common.models import BaseLoadTestResult

ResultT = TypeVar("ResultT", bound=BaseLoadTestResult)


class RedisResultStore(Generic[ResultT]):
    """Generic store keyed by ``test_id``, parametrized by result model.

    Pass the result model class at construction time so ``get`` and
    ``list_recent`` can deserialize JSON blobs back into typed Pydantic
    models. Using a runtime class argument (rather than Pydantic generics)
    keeps the implementation ordinary Python.
    """

    def __init__(
        self,
        redis: Any,
        key_prefix: str,
        result_model: type[ResultT],
        ttl_seconds: int = 86400,
    ) -> None:
        self._redis = redis
        self._prefix = key_prefix
        self._model = result_model
        self._ttl = ttl_seconds

    def _result_key(self, test_id: str) -> str:
        return f"{self._prefix}:results:{test_id}"

    @property
    def _recent_key(self) -> str:
        return f"{self._prefix}:recent"

    async def save(self, result: ResultT) -> None:
        """Persist a result and add it to the recency index."""
        key = self._result_key(result.test_id)
        blob = result.model_dump_json()
        await self._redis.set(key, blob, ex=self._ttl)
        await self._redis.zadd(self._recent_key, {result.test_id: time.time()})
        await self._redis.expire(self._recent_key, self._ttl)

    async def get(self, test_id: str) -> ResultT | None:
        """Fetch a single result, or ``None`` if missing/expired."""
        blob = await self._redis.get(self._result_key(test_id))
        if blob is None:
            return None
        if isinstance(blob, bytes):
            blob = blob.decode("utf-8")
        return self._model.model_validate_json(blob)

    async def list_recent(self, limit: int) -> list[ResultT]:
        """Return up to ``limit`` results, newest first.

        Expired index entries (where the underlying blob's TTL fired but
        the ZSET member remains) are skipped AND removed from the index in
        a single ``ZREM`` so the index doesn't drift out of sync with the
        keyspace. The current call may return one short if a stale entry
        is hit; the next call sees a clean index.
        """
        if limit <= 0:
            return []
        members = await self._redis.zrevrange(self._recent_key, 0, limit - 1)
        if not members:
            return []
        decoded_ids = [
            m.decode("utf-8") if isinstance(m, bytes) else m for m in members
        ]
        keys = [self._result_key(tid) for tid in decoded_ids]
        blobs = await self._redis.mget(keys)
        results: list[ResultT] = []
        stale_ids: list[str] = []
        for tid, blob in zip(decoded_ids, blobs):
            if blob is None:
                stale_ids.append(tid)
                continue
            if isinstance(blob, bytes):
                blob = blob.decode("utf-8")
            results.append(self._model.model_validate_json(blob))
        if stale_ids:
            await self._redis.zrem(self._recent_key, *stale_ids)
        return results

    async def delete(self, test_id: str) -> None:
        """Remove a result from both the KV store and the recency index."""
        await self._redis.delete(self._result_key(test_id))
        await self._redis.zrem(self._recent_key, test_id)

    async def aclose(self) -> None:
        """Close the underlying redis client.

        Call this before the event loop tears down so the client's
        ``__del__`` doesn't fire on a closed loop and emit an ugly
        ``RuntimeError`` traceback. Safe to call on stores backed by
        clients that don't implement ``aclose`` (e.g. test mocks).
        """
        closer = getattr(self._redis, "aclose", None)
        if closer is None:
            return
        try:
            await closer()
        except Exception:
            pass
