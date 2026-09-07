"""
Unit tests for ``RequestMetricsService`` and ``EndpointMetrics``.

Pure in-memory logic — no app/client. The integration coverage that
proves the middleware actually populates the service lives in
``tests/services/test_performance_middleware.py``.
"""

from app.components.backend.middleware.performance import (
    EndpointMetrics,
    RequestMetricsService,
)


class TestEndpointMetrics:
    def test_record_updates_counters(self) -> None:
        m = EndpointMetrics()
        m.record(10.0)
        m.record(20.0)
        m.record(30.0)
        assert m.count == 3
        assert m.total_ms == 60.0
        assert m.min_ms == 10.0
        assert m.max_ms == 30.0
        assert m.avg_ms == 20.0
        assert m.last_request_at is not None

    def test_recent_window_is_bounded(self) -> None:
        """``deque(maxlen=100)`` caps recent-samples memory."""
        m = EndpointMetrics()
        for i in range(150):
            m.record(float(i))
        assert len(m.recent_ms) == 100
        # Min/max are running totals, not window-bound — full range stays visible.
        assert m.min_ms == 0.0
        assert m.max_ms == 149.0
        # ``count`` is unbounded, total still tracks every sample.
        assert m.count == 150

    def test_percentiles_with_one_sample_fall_back_to_max(self) -> None:
        m = EndpointMetrics()
        m.record(42.0)
        assert m.p95_ms == 42.0
        assert m.p99_ms == 42.0

    def test_percentiles_with_full_window(self) -> None:
        m = EndpointMetrics()
        for i in range(1, 101):
            m.record(float(i))
        # ``statistics.quantiles`` returns the cut-points; p95 is between 95 and 96.
        assert 94.0 <= m.p95_ms <= 96.0
        assert 98.0 <= m.p99_ms <= 100.0


class TestRequestMetricsService:
    def test_empty_summary(self) -> None:
        service = RequestMetricsService()
        summary = service.get_summary_stats()
        assert summary["total_requests"] == 0
        assert summary["tracked_endpoints"] == 0
        assert summary["slowest_endpoint"] is None

    def test_record_buckets_by_method_and_pattern(self) -> None:
        service = RequestMetricsService()
        service.record("GET", "/users/{id}", 150.0)
        service.record("GET", "/users/{id}", 100.0)
        service.record("POST", "/users", 200.0)

        all_metrics = service.get_all_metrics()
        assert "GET /users/{id}" in all_metrics
        assert "POST /users" in all_metrics
        assert all_metrics["GET /users/{id}"]["count"] == 2
        assert all_metrics["GET /users/{id}"]["avg_ms"] == 125.0
        assert all_metrics["POST /users"]["count"] == 1

    def test_summary_identifies_slowest_endpoint(self) -> None:
        service = RequestMetricsService()
        service.record("GET", "/fast", 10.0)
        service.record("GET", "/slow", 500.0)

        summary = service.get_summary_stats()
        assert summary["total_requests"] == 2
        assert summary["tracked_endpoints"] == 2
        assert summary["slowest_endpoint"]["endpoint"] == "GET /slow"
        assert summary["slowest_endpoint"]["avg_ms"] == 500.0

    def test_reset_clears_all_state(self) -> None:
        service = RequestMetricsService()
        service.record("GET", "/foo", 50.0)
        assert service.get_summary_stats()["total_requests"] == 1
        service.reset()
        assert service.get_summary_stats()["total_requests"] == 0
        assert service.get_all_metrics() == {}
