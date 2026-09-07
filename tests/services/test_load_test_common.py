"""
Unit tests for shared load-test infrastructure (common models, analysis
helpers, Redis result storage).

These tests pin the contract for code shared between the worker
load-test service and the HTTP load-test service. They are written
TDD-first: the modules under test do not yet exist, and these tests
describe the surface the implementation must satisfy.
"""

import json
from unittest.mock import AsyncMock

from pydantic import ValidationError
import pytest

from app.services.load_test.common.analysis import (
    RatingBands,
    RecommendationThresholds,
    build_recommendations,
    rate_bucket,
)
from app.services.load_test.common.models import (
    BaseLoadTestConfiguration,
    BaseLoadTestMetrics,
    BaseLoadTestResult,
    LoadTestAnalysis,
    PerformanceAnalysisBase,
    ValidationStatus,
)
from app.services.load_test.common.storage import RedisResultStore


class _FixtureConfig(BaseLoadTestConfiguration):
    """Tiny subclass used to exercise generic envelope behavior."""

    label: str = "fixture"


class _FixtureMetrics(BaseLoadTestMetrics):
    """Tiny metrics subclass — no extra fields, just for typing."""


class _FixtureResult(BaseLoadTestResult):
    """Concrete result subclass with typed config/metrics."""

    configuration: _FixtureConfig
    metrics: _FixtureMetrics


def _valid_metrics(**overrides) -> _FixtureMetrics:
    """Build a metrics object with valid defaults; override individual fields."""
    defaults = {
        "tasks_sent": 100,
        "tasks_completed": 100,
        "tasks_failed": 0,
        "total_duration_seconds": 1.0,
        "overall_throughput": 100.0,
        "failure_rate_percent": 0.0,
        "completion_percentage": 100.0,
    }
    defaults.update(overrides)
    return _FixtureMetrics(**defaults)


def _valid_result(**overrides) -> _FixtureResult:
    """Build a successful result; override fields as needed."""
    defaults = {
        "status": "completed",
        "test_id": "tid-1",
        "configuration": _FixtureConfig(test_id="tid-1"),
        "metrics": _valid_metrics(),
        "error": None,
    }
    defaults.update(overrides)
    return _FixtureResult(**defaults)


class TestBaseLoadTestConfiguration:
    """BaseLoadTestConfiguration is a minimal envelope — only test_id."""

    def test_test_id_is_optional(self):
        config = BaseLoadTestConfiguration()
        assert config.test_id is None

    def test_subclass_can_add_fields(self):
        config = _FixtureConfig(test_id="abc", label="hello")
        assert config.test_id == "abc"
        assert config.label == "hello"


class TestBaseLoadTestMetrics:
    """Cross-field consistency validators are the whole reason this base exists."""

    def test_valid_metrics(self):
        metrics = _valid_metrics()
        assert metrics.tasks_sent == 100
        assert metrics.completion_percentage == 100.0

    def test_completed_cannot_exceed_sent(self):
        with pytest.raises(ValidationError, match="completed"):
            _valid_metrics(tasks_sent=10, tasks_completed=11)

    def test_failed_cannot_exceed_sent(self):
        with pytest.raises(ValidationError, match="failed"):
            _valid_metrics(tasks_sent=10, tasks_failed=11)

    def test_failure_rate_must_match_counts(self):
        # 5 failed of 100 sent = 5.0%; declaring 50% is a lie
        with pytest.raises(ValidationError, match="failure rate"):
            _valid_metrics(
                tasks_sent=100,
                tasks_completed=95,
                tasks_failed=5,
                failure_rate_percent=50.0,
            )

    def test_failure_rate_tolerates_small_float_drift(self):
        # 1 failed of 3 sent = 33.333...% — exact equality would fail
        m = _valid_metrics(
            tasks_sent=3,
            tasks_completed=2,
            tasks_failed=1,
            failure_rate_percent=33.3,
            completion_percentage=66.7,
        )
        assert m.failure_rate_percent == pytest.approx(33.3)

    def test_negative_values_rejected(self):
        with pytest.raises(ValidationError):
            _valid_metrics(tasks_sent=-1)
        with pytest.raises(ValidationError):
            _valid_metrics(total_duration_seconds=-0.1)

    def test_percentage_fields_capped_at_100(self):
        with pytest.raises(ValidationError):
            _valid_metrics(completion_percentage=101.0)
        with pytest.raises(ValidationError):
            _valid_metrics(failure_rate_percent=100.1)


class TestBaseLoadTestResult:
    """Result envelope — status/error consistency is the load-bearing rule."""

    def test_completed_status_does_not_require_error(self):
        result = _valid_result()
        assert result.status == "completed"
        assert result.error is None

    def test_failed_status_requires_error(self):
        with pytest.raises(ValidationError, match="error"):
            _valid_result(status="failed", error=None)

    def test_failed_status_with_error_is_valid(self):
        result = _valid_result(status="failed", error="boom")
        assert result.status == "failed"
        assert result.error == "boom"

    def test_status_pattern_rejects_unknown_values(self):
        with pytest.raises(ValidationError):
            _valid_result(status="pending")  # not in literal set

    def test_subclass_preserves_typed_config_and_metrics(self):
        result = _valid_result()
        assert isinstance(result.configuration, _FixtureConfig)
        assert isinstance(result.metrics, _FixtureMetrics)

    def test_analysis_is_optional(self):
        result = _valid_result()
        assert result.analysis is None

    def test_analysis_attaches(self):
        analysis = LoadTestAnalysis(
            performance_analysis=PerformanceAnalysisBase(
                throughput_rating="excellent",
                efficiency_rating="excellent",
            ),
            validation_status=ValidationStatus(),
            recommendations=[],
        )
        result = _valid_result(analysis=analysis)
        assert result.analysis is analysis


class TestPerformanceAnalysisBase:
    """The two ratings every load test has — throughput and efficiency."""

    def test_valid_ratings(self):
        pa = PerformanceAnalysisBase(
            throughput_rating="good",
            efficiency_rating="excellent",
        )
        assert pa.throughput_rating == "good"

    def test_rating_pattern_enforced(self):
        with pytest.raises(ValidationError):
            PerformanceAnalysisBase(
                throughput_rating="amazing",  # not in allowed set
                efficiency_rating="good",
            )


class TestValidationStatus:
    """Defaults are deliberate — an unanalyzed run reads as 'unknown'."""

    def test_defaults(self):
        vs = ValidationStatus()
        assert vs.test_type_verified is False
        assert vs.expected_metrics_present is False
        assert vs.performance_signature_match == "unknown"
        assert vs.issues == []

    def test_signature_match_pattern(self):
        with pytest.raises(ValidationError):
            ValidationStatus(performance_signature_match="ok")

    def test_issues_append(self):
        vs = ValidationStatus(issues=["one", "two"])
        assert vs.issues == ["one", "two"]


class TestRateBucket:
    """rate_bucket is the only place rating thresholds get evaluated.

    Both worker and HTTP services pass their own RatingBands; the function
    itself stays generic.
    """

    def test_higher_is_better_excellent(self):
        bands = RatingBands(excellent=50, good=20, fair=10, higher_is_better=True)
        assert rate_bucket(75.0, bands) == "excellent"
        assert rate_bucket(50.0, bands) == "excellent"  # boundary inclusive

    def test_higher_is_better_good_fair_poor(self):
        bands = RatingBands(excellent=50, good=20, fair=10, higher_is_better=True)
        assert rate_bucket(30.0, bands) == "good"
        assert rate_bucket(15.0, bands) == "fair"
        assert rate_bucket(5.0, bands) == "poor"
        assert rate_bucket(0.0, bands) == "poor"

    def test_lower_is_better_latency_style(self):
        # HTTP p95 latency: <=100ms excellent, <=500ms good, <=2000ms fair
        bands = RatingBands(excellent=100, good=500, fair=2000, higher_is_better=False)
        assert rate_bucket(50.0, bands) == "excellent"
        assert rate_bucket(100.0, bands) == "excellent"  # boundary inclusive
        assert rate_bucket(300.0, bands) == "good"
        assert rate_bucket(1500.0, bands) == "fair"
        assert rate_bucket(5000.0, bands) == "poor"

    def test_boundary_inclusivity_documented(self):
        """Boundary values fall into the BETTER tier — pinned to prevent drift."""
        bands_hi = RatingBands(excellent=50, good=20, fair=10, higher_is_better=True)
        assert rate_bucket(20.0, bands_hi) == "good"  # not "fair"
        bands_lo = RatingBands(
            excellent=100, good=500, fair=2000, higher_is_better=False
        )
        assert rate_bucket(500.0, bands_lo) == "good"  # not "fair"


class TestBuildRecommendations:
    """Recommendation messages are shared verbatim across services; only
    thresholds differ."""

    def _worker_thresholds(self) -> RecommendationThresholds:
        return RecommendationThresholds(
            low_throughput=10.0,
            high_failure_rate=5.0,
            long_duration=60.0,
            small_task_count=200,
        )

    def test_no_recommendations_when_healthy(self):
        recs = build_recommendations(
            throughput=100.0,
            failure_rate=0.0,
            duration=1.0,
            tasks_sent=1000,
            thresholds=self._worker_thresholds(),
        )
        assert recs == []

    def test_low_throughput_recommendation(self):
        recs = build_recommendations(
            throughput=5.0,
            failure_rate=0.0,
            duration=1.0,
            tasks_sent=1000,
            thresholds=self._worker_thresholds(),
        )
        assert any("throughput" in r.lower() for r in recs)

    def test_high_failure_rate_recommendation_includes_rate(self):
        recs = build_recommendations(
            throughput=100.0,
            failure_rate=12.5,
            duration=1.0,
            tasks_sent=1000,
            thresholds=self._worker_thresholds(),
        )
        failure_recs = [r for r in recs if "failure" in r.lower()]
        assert len(failure_recs) == 1
        assert "12.5" in failure_recs[0]

    def test_saturation_recommendation_requires_long_duration_and_small_count(self):
        # Long duration alone does NOT trigger if task count is large
        recs = build_recommendations(
            throughput=100.0,
            failure_rate=0.0,
            duration=120.0,
            tasks_sent=5000,
            thresholds=self._worker_thresholds(),
        )
        assert not any("saturation" in r.lower() for r in recs)

        # Long duration + small count DOES trigger
        recs = build_recommendations(
            throughput=100.0,
            failure_rate=0.0,
            duration=120.0,
            tasks_sent=50,
            thresholds=self._worker_thresholds(),
        )
        assert any("saturation" in r.lower() for r in recs)

    def test_http_thresholds_change_when_recommendations_fire(self):
        """Same metrics, different thresholds -> different recs.

        HTTP tunes "low throughput" to ~100 req/s rather than worker's 10/s.
        100 req/s is healthy for a worker but anemic for an HTTP server.
        """
        http_thresholds = RecommendationThresholds(
            low_throughput=100.0,
            high_failure_rate=1.0,
            long_duration=30.0,
            small_task_count=1000,
        )
        recs = build_recommendations(
            throughput=50.0,
            failure_rate=0.0,
            duration=1.0,
            tasks_sent=10000,
            thresholds=http_thresholds,
        )
        assert any("throughput" in r.lower() for r in recs)


class TestRedisResultStore:
    """Generic Redis storage shared between worker and HTTP services.

    Uses Redis ZSET (recent index) + JSON-blob string keys (the results).
    Tests use AsyncMock for the redis client — mirroring the pattern in
    test_load_test_service.py.
    """

    @pytest.fixture
    def redis_mock(self):
        m = AsyncMock()
        # In-memory backing for SET/GET/MGET round-trips
        m._kv = {}
        m._zset = []  # list of (score, member)

        async def _set(key, value, ex=None):
            m._kv[key] = value
            return True

        async def _get(key):
            return m._kv.get(key)

        async def _mget(keys):
            return [m._kv.get(k) for k in keys]

        async def _zadd(key, mapping):
            for member, score in mapping.items():
                m._zset.append((score, member))
            return len(mapping)

        async def _zrevrange(key, start, stop):
            sorted_members = [
                member
                for _, member in sorted(m._zset, key=lambda x: x[0], reverse=True)
            ]
            return sorted_members[start : stop + 1]

        async def _expire(key, seconds):
            return True

        async def _delete(*keys):
            for k in keys:
                m._kv.pop(k, None)
            return len(keys)

        async def _zrem(key, *members):
            m._zset = [(s, mem) for (s, mem) in m._zset if mem not in members]
            return len(members)

        m.set.side_effect = _set
        m.get.side_effect = _get
        m.mget.side_effect = _mget
        m.zadd.side_effect = _zadd
        m.zrevrange.side_effect = _zrevrange
        m.expire.side_effect = _expire
        m.delete.side_effect = _delete
        m.zrem.side_effect = _zrem
        return m

    @pytest.fixture
    def store(self, redis_mock) -> RedisResultStore:
        return RedisResultStore(
            redis=redis_mock,
            key_prefix="test_lt",
            result_model=_FixtureResult,
            ttl_seconds=3600,
        )

    async def test_save_and_get_round_trip(self, store, redis_mock):
        result = _valid_result(test_id="abc-123")
        await store.save(result)
        loaded = await store.get("abc-123")
        assert loaded is not None
        assert loaded.test_id == "abc-123"
        assert loaded.status == "completed"
        assert loaded.metrics.tasks_sent == 100

    async def test_save_writes_result_key_with_prefix(self, store, redis_mock):
        result = _valid_result(test_id="abc-123")
        await store.save(result)
        assert "test_lt:results:abc-123" in redis_mock._kv

    async def test_save_stores_valid_json(self, store, redis_mock):
        result = _valid_result(test_id="abc-123")
        await store.save(result)
        blob = redis_mock._kv["test_lt:results:abc-123"]
        # Decode whether bytes or str
        if isinstance(blob, bytes):
            blob = blob.decode("utf-8")
        parsed = json.loads(blob)
        assert parsed["test_id"] == "abc-123"
        assert parsed["status"] == "completed"

    async def test_save_indexes_in_recent_zset(self, store, redis_mock):
        result = _valid_result(test_id="abc-123")
        await store.save(result)
        members = [m for _, m in redis_mock._zset]
        assert "abc-123" in members

    async def test_save_sets_ttl_on_both_keys(self, store, redis_mock):
        result = _valid_result(test_id="abc-123")
        await store.save(result)
        # Either ex= on set, or explicit expire calls — implementation choice,
        # but the TTL must be applied. Acceptable evidence: expire was called
        # with the result key OR set was called with ex=3600.
        expire_calls = [c.args for c in redis_mock.expire.call_args_list]
        set_calls = redis_mock.set.call_args_list
        ttl_applied_to_result = any(
            "test_lt:results:abc-123" in args for args in expire_calls
        ) or any(c.kwargs.get("ex") == 3600 for c in set_calls)
        assert ttl_applied_to_result, "TTL must be applied to result key"

    async def test_get_returns_none_for_missing(self, store):
        loaded = await store.get("does-not-exist")
        assert loaded is None

    async def test_list_recent_returns_newest_first(self, store):
        for tid in ["t1", "t2", "t3"]:
            await store.save(_valid_result(test_id=tid))
        recent = await store.list_recent(limit=10)
        ids = [r.test_id for r in recent]
        assert ids == ["t3", "t2", "t1"]

    async def test_list_recent_respects_limit(self, store):
        for tid in ["t1", "t2", "t3", "t4", "t5"]:
            await store.save(_valid_result(test_id=tid))
        recent = await store.list_recent(limit=2)
        assert len(recent) == 2
        assert recent[0].test_id == "t5"
        assert recent[1].test_id == "t4"

    async def test_list_recent_empty_when_nothing_stored(self, store):
        recent = await store.list_recent(limit=10)
        assert recent == []

    async def test_delete_removes_from_both_kv_and_zset(self, store, redis_mock):
        await store.save(_valid_result(test_id="goner"))
        assert "test_lt:results:goner" in redis_mock._kv
        await store.delete("goner")
        assert "test_lt:results:goner" not in redis_mock._kv
        members = [m for _, m in redis_mock._zset]
        assert "goner" not in members

    async def test_aclose_delegates_to_redis(self, store, redis_mock):
        """``aclose`` must call the underlying client's ``aclose`` so the
        CLI can shut down cleanly before the event loop tears down."""
        redis_mock.aclose = AsyncMock(return_value=None)
        await store.aclose()
        redis_mock.aclose.assert_awaited_once()

    async def test_aclose_safe_when_client_lacks_aclose(self, store, redis_mock):
        """Test mocks may not implement ``aclose``; the store must tolerate
        it rather than raising AttributeError."""
        if hasattr(redis_mock, "aclose"):
            del redis_mock.aclose
        # Should not raise
        await store.aclose()

    async def test_list_recent_removes_stale_ids_from_index(self, store, redis_mock):
        """When a result blob has been evicted (TTL fired on the value but
        the ZSET member remains), ``list_recent`` must skip it AND ZREM the
        dangling member so the index doesn't drift out of sync with the
        keyspace over time."""
        await store.save(_valid_result(test_id="alive-1"))
        await store.save(_valid_result(test_id="stale-1"))
        await store.save(_valid_result(test_id="alive-2"))

        # Simulate selective TTL: drop just the result blob for stale-1.
        redis_mock._kv.pop("test_lt:results:stale-1")

        recent = await store.list_recent(limit=10)
        live_ids = [r.test_id for r in recent]
        assert "stale-1" not in live_ids
        assert set(live_ids) == {"alive-1", "alive-2"}

        # The stale id was removed from the index.
        index_members = [m for _, m in redis_mock._zset]
        assert "stale-1" not in index_members
        assert "alive-1" in index_members
        assert "alive-2" in index_members
