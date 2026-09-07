"""
Tests for CacheService (in-memory dict backend).

All ops are async. These tests exercise the dict-backed path
directly. The Redis-backed singleton is bypassed for the test
session via the ``_use_dict_backed_cache`` fixture in
``tests/conftest.py`` — Redis clients bind to an event loop at
import, and pytest-asyncio creates a fresh loop per test, so
running tests through the live Redis singleton breaks. Adding
Redis coverage would need a dedicated test module that opts out
of that fixture.
"""

import asyncio

import pytest

from app.core.cache import CacheService


class TestCacheService:
    @pytest.mark.asyncio
    async def test_set_and_get(self) -> None:
        cache = CacheService()
        await cache.set("key1", {"data": 42})
        assert await cache.get("key1") == {"data": 42}

    @pytest.mark.asyncio
    async def test_get_missing_key_returns_none(self) -> None:
        cache = CacheService()
        assert await cache.get("nonexistent") is None

    @pytest.mark.asyncio
    async def test_ttl_expiry(self) -> None:
        cache = CacheService()
        await cache.set("key1", "value", ttl=0)
        await asyncio.sleep(0.01)
        assert await cache.get("key1") is None

    @pytest.mark.asyncio
    async def test_default_ttl(self) -> None:
        cache = CacheService(default_ttl=1)
        await cache.set("key1", "value")
        assert await cache.get("key1") == "value"
        await asyncio.sleep(1.1)
        assert await cache.get("key1") is None

    @pytest.mark.asyncio
    async def test_invalidate(self) -> None:
        cache = CacheService()
        await cache.set("key1", "value")
        await cache.invalidate("key1")
        assert await cache.get("key1") is None

    @pytest.mark.asyncio
    async def test_invalidate_missing_key_no_error(self) -> None:
        cache = CacheService()
        await cache.invalidate("nonexistent")  # should not raise

    @pytest.mark.asyncio
    async def test_clear(self) -> None:
        cache = CacheService()
        await cache.set("a", 1)
        await cache.set("b", 2)
        await cache.clear()
        assert await cache.get("a") is None
        assert await cache.get("b") is None

    @pytest.mark.asyncio
    async def test_overwrite_existing_key(self) -> None:
        cache = CacheService()
        await cache.set("key1", "old")
        await cache.set("key1", "new")
        assert await cache.get("key1") == "new"

    @pytest.mark.asyncio
    async def test_custom_ttl_overrides_default(self) -> None:
        cache = CacheService(default_ttl=300)
        await cache.set("key1", "value", ttl=0)
        await asyncio.sleep(0.01)
        assert await cache.get("key1") is None

    @pytest.mark.asyncio
    async def test_set_ttl_zero_deletes_existing_key(self) -> None:
        """``ttl=0`` after a normal set must remove the key, not write a
        stale entry that lingers until the next read."""
        cache = CacheService()
        await cache.set("key1", "value", ttl=60)
        assert await cache.get("key1") == "value"
        await cache.set("key1", "newer", ttl=0)
        # No write, no leftover from the previous set — the key is gone.
        assert await cache.get("key1") is None
        # And the underlying store has dropped the entry entirely, not
        # just marked it stale.
        assert "key1" not in cache._store  # type: ignore[operator]

    @pytest.mark.asyncio
    async def test_set_negative_ttl_deletes_existing_key(self) -> None:
        cache = CacheService()
        await cache.set("key1", "value", ttl=60)
        await cache.set("key1", "newer", ttl=-1)
        assert await cache.get("key1") is None
        assert "key1" not in cache._store  # type: ignore[operator]

    @pytest.mark.asyncio
    async def test_invalidate_prefix_dict_backend(self) -> None:
        """Prefix invalidation works on the dict path too."""
        cache = CacheService()
        await cache.set("view:1:overview:7", "a")
        await cache.set("view:1:github:14", "b")
        await cache.set("view:2:overview:7", "c")
        removed = await cache.invalidate_prefix("view:1:")
        assert removed == 2
        assert await cache.get("view:1:overview:7") is None
        assert await cache.get("view:1:github:14") is None
        assert await cache.get("view:2:overview:7") == "c"
