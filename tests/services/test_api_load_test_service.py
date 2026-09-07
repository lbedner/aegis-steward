"""
Unit tests for ``APILoadTestService``.

The service drives concurrent HTTP requests through ``httpx.AsyncClient``
and collects per-request latency, status codes, and error samples. Tests
use ``httpx.ASGITransport`` against tiny in-test FastAPI apps to avoid
binding ports while still exercising the real httpx code path.

Storage is tested through dependency injection: a ``RedisResultStore``
backed by an ``AsyncMock`` redis (mirroring the pattern used in
``test_load_test_common.py``).
"""

import asyncio

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import pytest

from app.services.load_test.api.models import (
    APILoadTestConfiguration,
    APILoadTestResult,
)
from app.services.load_test.api.service import APILoadTestService
from app.services.load_test.common.storage import RedisResultStore


def _basic_app() -> FastAPI:
    app = FastAPI()

    @app.get("/health")
    async def health():
        return {"ok": True}

    @app.get("/slow")
    async def slow():
        await asyncio.sleep(0.01)
        return {"ok": True}

    @app.get("/error")
    async def error():
        raise HTTPException(status_code=500, detail="boom")

    class Echo(BaseModel):
        value: int

    @app.post("/echo")
    async def echo(body: Echo):
        return {"received": body.value}

    return app


def _concurrency_tracking_app() -> tuple[FastAPI, dict]:
    """App + state dict that records max-concurrent in-flight requests."""
    state = {"in_flight": 0, "max_in_flight": 0}
    app = FastAPI()

    @app.get("/track")
    async def track():
        state["in_flight"] += 1
        state["max_in_flight"] = max(state["max_in_flight"], state["in_flight"])
        await asyncio.sleep(0.02)
        state["in_flight"] -= 1
        return {"ok": True}

    return app, state


class TestAPILoadTestServiceRun:
    """Behavioral tests for ``service.run()`` against in-process ASGI apps."""

    async def test_basic_success_against_health(self):
        service = APILoadTestService()
        app = _basic_app()
        config = APILoadTestConfiguration(
            test_id="t1",
            method="GET",
            path="/health",
            requests=20,
            clients=5,
            in_process=True,
        )
        result = await service.run(config, app=app)
        assert isinstance(result, APILoadTestResult)
        assert result.status == "completed"
        assert result.metrics.tasks_sent == 20
        assert result.metrics.tasks_completed == 20
        assert result.metrics.tasks_failed == 0
        assert result.metrics.status_codes == {200: 20}
        assert result.metrics.overall_throughput > 0

    async def test_percentile_fields_present_and_ordered(self):
        service = APILoadTestService()
        app = _basic_app()
        config = APILoadTestConfiguration(
            test_id="t2",
            method="GET",
            path="/slow",
            requests=30,
            clients=5,
            in_process=True,
        )
        result = await service.run(config, app=app)
        m = result.metrics
        assert m.latency_ms_p50 > 0
        assert m.latency_ms_p95 >= m.latency_ms_p50
        assert m.latency_ms_p99 >= m.latency_ms_p95
        assert m.latency_ms_max >= m.latency_ms_p99

    async def test_5xx_responses_counted_as_failures(self):
        service = APILoadTestService()
        app = _basic_app()
        config = APILoadTestConfiguration(
            test_id="t3",
            method="GET",
            path="/error",
            requests=10,
            clients=2,
            in_process=True,
        )
        result = await service.run(config, app=app)
        assert result.metrics.tasks_failed == 10
        assert result.metrics.tasks_completed == 0
        assert result.metrics.status_codes == {500: 10}
        assert result.metrics.failure_rate_percent == 100.0

    async def test_mixed_success_and_failure_rates(self):
        """The endpoint flips between OK and error so we can verify the
        counts/percentages reflect real outcomes — not always 0% or 100%."""
        app = FastAPI()
        call_count = {"n": 0}

        @app.get("/flaky")
        async def flaky():
            call_count["n"] += 1
            if call_count["n"] % 4 == 0:
                raise HTTPException(status_code=500)
            return {"ok": True}

        service = APILoadTestService()
        config = APILoadTestConfiguration(
            test_id="t4",
            method="GET",
            path="/flaky",
            requests=20,
            clients=1,  # serial so 1-of-4 fails deterministically
            in_process=True,
        )
        result = await service.run(config, app=app)
        assert result.metrics.tasks_sent == 20
        assert result.metrics.tasks_failed == 5
        assert result.metrics.tasks_completed == 15
        assert result.metrics.failure_rate_percent == 25.0
        assert result.metrics.status_codes == {200: 15, 500: 5}

    async def test_post_with_dict_payload(self):
        service = APILoadTestService()
        app = _basic_app()
        config = APILoadTestConfiguration(
            test_id="t5",
            method="POST",
            path="/echo",
            requests=5,
            clients=2,
            payload={"value": 42},
            in_process=True,
        )
        result = await service.run(config, app=app)
        assert result.metrics.tasks_completed == 5
        assert result.metrics.status_codes == {200: 5}

    async def test_custom_headers_attached(self):
        from fastapi import Header

        seen_headers: list[str | None] = []
        app = FastAPI()

        @app.get("/echo-header")
        async def echo_header(x_custom: str | None = Header(default=None)):
            seen_headers.append(x_custom)
            return {"x_custom": x_custom}

        service = APILoadTestService()
        config = APILoadTestConfiguration(
            test_id="t6",
            method="GET",
            path="/echo-header",
            requests=3,
            clients=1,
            headers={"x-custom": "hello"},
            in_process=True,
        )
        result = await service.run(config, app=app)
        assert result.metrics.tasks_completed == 3
        assert seen_headers == ["hello", "hello", "hello"]

    async def test_concurrency_cap_honored(self):
        app, state = _concurrency_tracking_app()
        service = APILoadTestService()
        config = APILoadTestConfiguration(
            test_id="t7",
            method="GET",
            path="/track",
            requests=20,
            clients=4,
            in_process=True,
        )
        await service.run(config, app=app)
        assert state["max_in_flight"] <= 4, (
            f"Service exceeded configured concurrency: "
            f"max_in_flight={state['max_in_flight']}, clients=4"
        )

    async def test_run_populates_test_id_and_timestamps(self):
        service = APILoadTestService()
        app = _basic_app()
        config = APILoadTestConfiguration(
            test_id="given-id",
            method="GET",
            path="/health",
            requests=5,
            clients=2,
            in_process=True,
        )
        result = await service.run(config, app=app)
        assert result.test_id == "given-id"
        assert result.start_time is not None
        assert result.end_time is not None

    async def test_run_generates_test_id_when_not_provided(self):
        service = APILoadTestService()
        app = _basic_app()
        config = APILoadTestConfiguration(
            method="GET",
            path="/health",
            requests=5,
            clients=2,
            in_process=True,
        )
        result = await service.run(config, app=app)
        assert result.test_id  # non-empty
        assert len(result.test_id) > 0

    async def test_in_process_requires_app(self):
        """``in_process=True`` without an app is a programmer error —
        fail loudly rather than silently fall back to out-of-process."""
        service = APILoadTestService()
        config = APILoadTestConfiguration(
            test_id="t8",
            method="GET",
            path="/health",
            requests=5,
            clients=2,
            in_process=True,
        )
        with pytest.raises(ValueError, match="app"):
            await service.run(config, app=None)


class TestAPILoadTestServiceStorage:
    """Storage delegation: when a store is injected, save/get/list_recent
    route through it; with no store, those methods are no-ops."""

    def _make_store_and_redis(self):
        from unittest.mock import AsyncMock

        redis = AsyncMock()
        redis._kv = {}
        redis._zset = []

        async def _set(key, value, ex=None):
            redis._kv[key] = value
            return True

        async def _get(key):
            return redis._kv.get(key)

        async def _mget(keys):
            return [redis._kv.get(k) for k in keys]

        async def _zadd(key, mapping):
            for member, score in mapping.items():
                redis._zset.append((score, member))
            return len(mapping)

        async def _zrevrange(key, start, stop):
            members = [
                m for _, m in sorted(redis._zset, key=lambda x: x[0], reverse=True)
            ]
            return members[start : stop + 1]

        async def _expire(key, seconds):
            return True

        async def _delete(*keys):
            for k in keys:
                redis._kv.pop(k, None)
            return len(keys)

        async def _zrem(key, *members):
            redis._zset = [(s, m) for (s, m) in redis._zset if m not in members]
            return len(members)

        redis.set.side_effect = _set
        redis.get.side_effect = _get
        redis.mget.side_effect = _mget
        redis.zadd.side_effect = _zadd
        redis.zrevrange.side_effect = _zrevrange
        redis.expire.side_effect = _expire
        redis.delete.side_effect = _delete
        redis.zrem.side_effect = _zrem

        store = RedisResultStore(
            redis=redis,
            key_prefix="api_load_test",
            result_model=APILoadTestResult,
            ttl_seconds=3600,
        )
        return store, redis

    async def test_run_persists_result_when_store_provided(self):
        store, redis = self._make_store_and_redis()
        service = APILoadTestService(store=store)
        app = _basic_app()
        config = APILoadTestConfiguration(
            test_id="persisted",
            method="GET",
            path="/health",
            requests=5,
            clients=2,
            in_process=True,
        )
        await service.run(config, app=app)
        assert "api_load_test:results:persisted" in redis._kv

    async def test_run_does_not_persist_when_no_store(self):
        """No store -> result is returned but not saved anywhere. The
        caller can still inspect it; persistence is opt-in."""
        service = APILoadTestService()
        app = _basic_app()
        config = APILoadTestConfiguration(
            test_id="t9",
            method="GET",
            path="/health",
            requests=5,
            clients=2,
            in_process=True,
        )
        # Should not raise
        result = await service.run(config, app=app)
        assert result.test_id == "t9"

    async def test_get_result_delegates_to_store(self):
        store, _ = self._make_store_and_redis()
        service = APILoadTestService(store=store)
        app = _basic_app()
        config = APILoadTestConfiguration(
            test_id="findme",
            method="GET",
            path="/health",
            requests=5,
            clients=2,
            in_process=True,
        )
        await service.run(config, app=app)
        loaded = await service.get_result("findme")
        assert loaded is not None
        assert loaded.test_id == "findme"
        assert loaded.metrics.tasks_completed == 5

    async def test_get_result_returns_none_when_no_store(self):
        service = APILoadTestService()
        loaded = await service.get_result("anything")
        assert loaded is None

    async def test_list_recent_returns_recent_runs(self):
        store, _ = self._make_store_and_redis()
        service = APILoadTestService(store=store)
        app = _basic_app()
        for tid in ["r1", "r2", "r3"]:
            await service.run(
                APILoadTestConfiguration(
                    test_id=tid,
                    method="GET",
                    path="/health",
                    requests=3,
                    clients=1,
                    in_process=True,
                ),
                app=app,
            )
        recent = await service.list_recent(limit=10)
        ids = [r.test_id for r in recent]
        assert set(ids) == {"r1", "r2", "r3"}

    async def test_list_recent_returns_empty_when_no_store(self):
        service = APILoadTestService()
        recent = await service.list_recent(limit=10)
        assert recent == []


class TestProgressCallback:
    """``run`` accepts a ``progress_callback`` so the CLI can render a
    live progress bar for long runs. The callback fires once per
    completed request with ``(done, total)``."""

    async def test_callback_fires_once_per_request(self):
        seen: list[tuple[int, int]] = []

        def _cb(done: int, total: int) -> None:
            seen.append((done, total))

        service = APILoadTestService()
        app = _basic_app()
        config = APILoadTestConfiguration(
            test_id="pg1",
            method="GET",
            path="/health",
            requests=15,
            clients=5,
            in_process=True,
        )
        await service.run(config, app=app, progress_callback=_cb)
        assert len(seen) == 15
        # ``total`` is the same on every call
        assert {t for _, t in seen} == {15}
        # ``done`` reaches the total by the last call (order between
        # in-flight ticks isn't guaranteed under concurrency, but the
        # max value is)
        assert max(d for d, _ in seen) == 15

    async def test_callback_fires_for_failures_too(self):
        """The bar should advance whether a request succeeded or errored."""
        app = FastAPI()

        @app.get("/always-fail")
        async def always_fail():
            raise HTTPException(status_code=500)

        seen: list[tuple[int, int]] = []
        service = APILoadTestService()
        config = APILoadTestConfiguration(
            test_id="pg2",
            method="GET",
            path="/always-fail",
            requests=8,
            clients=2,
            in_process=True,
        )
        await service.run(
            config, app=app, progress_callback=lambda d, t: seen.append((d, t))
        )
        assert len(seen) == 8

    async def test_callback_optional(self):
        """Run without a callback works unchanged."""
        service = APILoadTestService()
        app = _basic_app()
        config = APILoadTestConfiguration(
            test_id="pg3",
            method="GET",
            path="/health",
            requests=5,
            clients=2,
            in_process=True,
        )
        result = await service.run(config, app=app)
        assert result.metrics.tasks_completed == 5


class TestPathParamSubstitution:
    """``APILoadTestConfiguration.path_params`` substitutes ``{name}``
    placeholders into the path before each request. Missing values fail
    fast with a clear error, naming which params still need values."""

    async def test_single_param_substituted(self):
        app = FastAPI()
        seen_paths: list[str] = []

        @app.get("/users/{user_id}")
        async def get_user(user_id: str):
            seen_paths.append(user_id)
            return {"id": user_id}

        service = APILoadTestService()
        config = APILoadTestConfiguration(
            test_id="pp1",
            method="GET",
            path="/users/{user_id}",
            path_params={"user_id": "abc-123"},
            requests=5,
            clients=1,
            in_process=True,
        )
        result = await service.run(config, app=app)
        assert result.metrics.tasks_completed == 5
        assert seen_paths == ["abc-123"] * 5

    async def test_multiple_params_substituted(self):
        app = FastAPI()

        @app.get("/items/{item_id}/owner/{user_id}")
        async def item_owner(item_id: str, user_id: str):
            return {"item": item_id, "user": user_id}

        service = APILoadTestService()
        config = APILoadTestConfiguration(
            test_id="pp2",
            method="GET",
            path="/items/{item_id}/owner/{user_id}",
            path_params={"item_id": "42", "user_id": "u-9"},
            requests=3,
            clients=1,
            in_process=True,
        )
        result = await service.run(config, app=app)
        assert result.metrics.tasks_completed == 3
        assert result.metrics.status_codes == {200: 3}

    async def test_unsubstituted_param_raises_value_error(self):
        """Sending a literal ``{name}`` to FastAPI 404s or 422s — confusing.
        Fail fast in the orchestrator with a message naming what's missing."""
        service = APILoadTestService()
        config = APILoadTestConfiguration(
            test_id="pp3",
            method="GET",
            path="/users/{user_id}",
            requests=5,
            clients=1,
            in_process=True,
        )

        # The error should fire BEFORE issuing any request — so a missing
        # app is irrelevant; we pass a real one for clarity.
        app = FastAPI()

        with pytest.raises(ValueError, match="user_id"):
            await service.run(config, app=app)

    async def test_unsubstituted_param_error_lists_all_missing(self):
        service = APILoadTestService()
        config = APILoadTestConfiguration(
            test_id="pp4",
            method="GET",
            path="/items/{item_id}/owner/{user_id}",
            path_params={"item_id": "only-one"},
            requests=5,
            clients=1,
            in_process=True,
        )
        app = FastAPI()
        with pytest.raises(ValueError) as exc_info:
            await service.run(config, app=app)
        assert "user_id" in str(exc_info.value)
        # item_id is satisfied; shouldn't appear in the missing list
        assert "item_id" not in str(exc_info.value).split(":", 1)[-1]

    async def test_path_without_params_works_unchanged(self):
        """No params in path => no substitution, no error, no behavior change."""
        service = APILoadTestService()
        app = _basic_app()
        config = APILoadTestConfiguration(
            test_id="pp5",
            method="GET",
            path="/health",
            requests=5,
            clients=1,
            in_process=True,
        )
        result = await service.run(config, app=app)
        assert result.metrics.tasks_completed == 5


class TestErrorSampleCapping:
    """The service caps stored error samples so a failure storm doesn't
    blow up memory or the JSON blob size in Redis."""

    async def test_error_samples_capped_at_100(self):
        app = FastAPI()

        @app.get("/always-fail")
        async def always_fail():
            raise HTTPException(status_code=500)

        service = APILoadTestService()
        config = APILoadTestConfiguration(
            test_id="cap",
            method="GET",
            path="/always-fail",
            requests=200,
            clients=10,
            in_process=True,
        )
        result = await service.run(config, app=app)
        assert result.metrics.tasks_failed == 200
        assert len(result.metrics.errors) <= 100
