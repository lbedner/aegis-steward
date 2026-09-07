"""
Unit tests for LoadTestService.

Tests business logic, data transformation, and analysis functions.
"""

import pickle
from unittest.mock import AsyncMock, MagicMock, patch

from pydantic import ValidationError
import pytest

from app.components.worker.constants import LoadTestTypes
from app.services.load_test import LoadTestConfiguration, LoadTestService
from app.services.load_test_models import (
    LoadTestResult,
    PerformanceAnalysis,
)


class TestLoadTestConfiguration:
    """Test LoadTestConfiguration class (legacy config handler)."""

    def test_default_configuration(self):
        """Test configuration with defaults."""
        config = LoadTestConfiguration()

        assert config.num_tasks >= 1
        assert config.task_type == LoadTestTypes.CPU_INTENSIVE
        assert config.batch_size >= 1
        assert config.delay_ms >= 0

    def test_configuration_bounds(self):
        """Test configuration value bounds enforcement."""
        # Test upper bounds - should raise ValidationError
        with pytest.raises(ValidationError):
            LoadTestConfiguration(batch_size=200, delay_ms=10000)

        # Test lower bounds - should raise ValidationError
        with pytest.raises(ValidationError):
            LoadTestConfiguration(num_tasks=0, batch_size=0, delay_ms=-100)

    def test_to_dict(self):
        """Test configuration serialization."""
        config = LoadTestConfiguration(
            num_tasks=100,
            task_type=LoadTestTypes.IO_SIMULATION,
            batch_size=20,
            delay_ms=50,
            target_queue="test_queue",
        )

        result = config.model_dump()

        assert result["num_tasks"] == 100
        assert result["task_type"] == "io_simulation"
        assert result["batch_size"] == 20
        assert result["delay_ms"] == 50
        assert result["target_queue"] == "test_queue"


class TestLoadTestServiceTestTypeInfo:
    """Test LoadTestService.get_test_type_info method."""

    def test_cpu_test_type_info(self):
        """Test CPU test type information."""
        info = LoadTestService.get_test_type_info(LoadTestTypes.CPU_INTENSIVE)

        assert info["name"] == "CPU Intensive"
        assert "fibonacci" in info["description"].lower()
        assert "fibonacci_n" in info["expected_metrics"]
        assert "cpu_operations" in info["expected_metrics"]
        assert "cpu bound" in info["performance_signature"].lower()

    def test_io_test_type_info(self):
        """Test I/O test type information."""
        info = LoadTestService.get_test_type_info(LoadTestTypes.IO_SIMULATION)

        assert info["name"] == "I/O Simulation"
        assert "async" in info["description"].lower()
        assert "simulated_delay_ms" in info["expected_metrics"]
        assert "io_operations" in info["expected_metrics"]
        assert "i/o bound" in info["performance_signature"].lower()

    def test_memory_test_type_info(self):
        """Test memory test type information."""
        info = LoadTestService.get_test_type_info(LoadTestTypes.MEMORY_OPERATIONS)

        assert info["name"] == "Memory Operations"
        assert "allocation" in info["description"].lower()
        assert "allocation_size" in info["expected_metrics"]
        assert "list_sum" in info["expected_metrics"]
        assert "memory bound" in info["performance_signature"].lower()

    def test_failure_test_type_info(self):
        """Test failure test type information."""
        info = LoadTestService.get_test_type_info(LoadTestTypes.FAILURE_TESTING)

        assert info["name"] == "Failure Testing"
        assert "error handling" in info["description"].lower()
        assert "failure_rate" in info["expected_metrics"]
        assert "resilience" in info["performance_signature"].lower()

    def test_unknown_test_type(self):
        """Test handling of unknown test types."""
        info = LoadTestService.get_test_type_info("unknown_type")

        assert info == {}  # Should return empty dict for unknown types


class TestLoadTestServiceAnalysis:
    """Test LoadTestService analysis methods."""

    def test_analyze_performance_excellent_throughput(self):
        """Test performance analysis with excellent throughput."""
        result_data = {
            "metrics": {
                "overall_throughput": 60.0,  # Excellent (>= 50)
                "tasks_sent": 100,
                "tasks_completed": 100,
                "total_duration_seconds": 10.0,
            }
        }

        analysis = LoadTestService._analyze_performance(result_data)

        assert analysis["throughput_rating"] == "excellent"
        assert analysis["efficiency_rating"] == "excellent"  # 100% completion
        assert analysis["queue_pressure"] == "low"  # < 30s duration

    def test_analyze_performance_poor_throughput(self):
        """Test performance analysis with poor throughput."""
        result_data = {
            "metrics": {
                "overall_throughput": 5.0,  # Poor (< 10)
                "tasks_sent": 100,
                "tasks_completed": 50,  # 50% completion
                "total_duration_seconds": 80.0,  # High queue pressure
            }
        }

        analysis = LoadTestService._analyze_performance(result_data)

        assert analysis["throughput_rating"] == "poor"
        assert analysis["efficiency_rating"] == "poor"  # 50% completion
        assert analysis["queue_pressure"] == "high"  # > 60s duration

    def test_analyze_performance_pydantic_models(self):
        """Test Pydantic-based performance analysis."""
        # Create a proper LoadTestResult
        from app.services.load_test_models import (
            LoadTestConfiguration as ConfigModel,
        )
        from app.services.load_test_models import (
            LoadTestMetrics,
        )

        config = ConfigModel(
            task_type=LoadTestTypes.CPU_INTENSIVE,
            num_tasks=100,
            batch_size=10,
            target_queue="load_test",
        )

        metrics = LoadTestMetrics(
            tasks_sent=100,
            tasks_completed=95,
            tasks_failed=5,
            total_duration_seconds=25.0,
            overall_throughput=25.0,  # Good throughput
            failure_rate_percent=5.0,
        )

        result = LoadTestResult(
            status="completed",
            test_id="test-123",
            configuration=config,
            metrics=metrics,
        )

        analysis = LoadTestService._analyze_performance_pydantic(result)

        assert isinstance(analysis, PerformanceAnalysis)
        assert analysis.throughput_rating == "good"  # 20 <= 25 < 50
        assert analysis.efficiency_rating == "excellent"  # 95% completion
        assert analysis.queue_pressure == "low"  # < 30s

    def test_generate_recommendations_low_throughput(self):
        """Test recommendations for low throughput."""
        result_data = {
            "metrics": {
                "overall_throughput": 5.0,  # Low
                "failure_rate_percent": 2.0,  # Acceptable
                "total_duration_seconds": 20.0,
                "tasks_sent": 100,
            }
        }

        recommendations = LoadTestService._generate_recommendations(result_data)

        assert len(recommendations) == 1
        assert "low throughput" in recommendations[0].lower()
        assert "worker concurrency" in recommendations[0].lower()

    def test_generate_recommendations_high_failure_rate(self):
        """Test recommendations for high failure rate."""
        result_data = {
            "metrics": {
                "overall_throughput": 20.0,  # Good
                "failure_rate_percent": 15.0,  # High
                "total_duration_seconds": 25.0,
                "tasks_sent": 100,
            }
        }

        recommendations = LoadTestService._generate_recommendations(result_data)

        assert len(recommendations) == 1
        assert "high failure rate" in recommendations[0].lower()
        assert "15.0%" in recommendations[0]
        assert "worker logs" in recommendations[0].lower()

    def test_generate_recommendations_queue_saturation(self):
        """Test recommendations for queue saturation."""
        result_data = {
            "metrics": {
                "overall_throughput": 15.0,  # Fair
                "failure_rate_percent": 2.0,  # Good
                "total_duration_seconds": 90.0,  # Long
                "tasks_sent": 50,  # Few tasks for the duration
            }
        }

        recommendations = LoadTestService._generate_recommendations(result_data)

        assert len(recommendations) == 1
        assert "queue saturation" in recommendations[0].lower()
        assert "smaller batches" in recommendations[0].lower()


@pytest.mark.asyncio
class TestLoadTestServiceIntegration:
    """Test LoadTestService integration with mocked dependencies."""

    @patch("app.components.worker.pools.create_pool")
    async def test_enqueue_load_test_success(self, mock_create_pool):
        """Test successful load test enqueueing."""
        # Mock pool and job
        mock_pool = AsyncMock()
        mock_job = MagicMock()
        mock_job.job_id = "test-job-123"
        mock_pool.enqueue_job.return_value = mock_job
        mock_pool.ping.return_value = True  # For cache validation
        mock_create_pool.return_value = mock_pool

        # Create configuration
        config = LoadTestConfiguration(
            num_tasks=50,
            task_type=LoadTestTypes.CPU_INTENSIVE,
            batch_size=10,
            target_queue="load_test",
        )

        # Test enqueueing
        task_id = await LoadTestService.enqueue_load_test(config)

        # Verify results
        assert task_id == "test-job-123"

        mock_pool.enqueue_job.assert_called_once_with(
            "load_test_orchestrator",
            _queue_name="arq:queue:load_test",
            num_tasks=50,
            task_type=LoadTestTypes.CPU_INTENSIVE,
            batch_size=10,
            delay_ms=0,
            target_queue="load_test",
        )
        mock_pool.aclose.assert_called_once()

    @patch("app.components.worker.pools.create_pool")
    async def test_enqueue_load_test_failure(self, mock_create_pool):
        """Test load test enqueueing failure."""
        # Clear cache to ensure fresh mock
        from app.components.worker.pools import clear_pool_cache

        await clear_pool_cache()

        # Mock create_pool to raise an exception
        mock_create_pool.side_effect = Exception("Redis connection failed")

        config = LoadTestConfiguration()

        # Should raise the exception since pool creation fails
        with pytest.raises(Exception, match="Redis connection failed"):
            await LoadTestService.enqueue_load_test(config)

        # No pool cleanup needed since create_pool failed

    @patch("app.components.worker.pools.create_pool")
    async def test_get_load_test_result_success(self, mock_create_pool):  # noqa
        """Test successful result retrieval with Pydantic validation."""
        # Clear cache to ensure fresh mock
        from app.components.worker.pools import clear_pool_cache

        await clear_pool_cache()

        # Mock Redis data (realistic orchestrator result)
        raw_result_data = {
            "test_id": "test-123",
            "task_type": "io_simulation",
            "tasks_sent": 10,
            "tasks_completed": 10,
            "tasks_failed": 0,
            "total_duration_seconds": 2.5,
            "overall_throughput_per_second": 4.0,
            "failure_rate_percent": 0.0,
            "completion_percentage": 100.0,
            "average_throughput_per_second": 4.0,
            "monitor_duration_seconds": 2.5,
            "batch_size": 10,
            "delay_ms": 0,
            "target_queue": "load_test",
            "start_time": "2023-01-01T10:00:00",
            "end_time": "2023-01-01T10:00:02.5",
        }

        # Mock arq result format: {"r": actual_result, "t": 1, "s": true, ...}
        arq_result = {"r": raw_result_data, "t": 1, "s": True}
        pickled_result = pickle.dumps(arq_result)

        # Mock pool
        mock_pool = AsyncMock()
        mock_pool.exists.return_value = True
        mock_pool.get.return_value = pickled_result
        mock_pool.ping.return_value = True  # For cache validation
        mock_pool.aclose.return_value = None  # Mock cleanup
        mock_create_pool.return_value = mock_pool

        # Test result retrieval
        result = await LoadTestService.get_load_test_result("test-123", "load_test")

        # Verify result structure
        assert result is not None
        assert result["status"] == "completed"
        assert result["test_id"] == "test-123"
        assert result["metrics"]["tasks_completed"] == 10
        assert result["metrics"]["overall_throughput"] == 4.0

        # Verify analysis was added
        assert "analysis" in result
        assert "performance_analysis" in result["analysis"]
        assert "recommendations" in result["analysis"]

        mock_pool.aclose.assert_called_once()

    @patch("app.components.worker.pools.create_pool")
    async def test_get_load_test_result_not_found(self, mock_create_pool):  # noqa
        """Test result retrieval when task doesn't exist."""
        # Clear cache to ensure fresh mock
        from app.components.worker.pools import clear_pool_cache

        await clear_pool_cache()

        # Mock pool with no results
        mock_pool = AsyncMock()
        mock_pool.exists.return_value = False
        mock_pool.ping.return_value = True  # For cache validation
        mock_pool.aclose.return_value = None  # Mock cleanup
        mock_create_pool.return_value = mock_pool

        result = await LoadTestService.get_load_test_result("nonexistent", "load_test")

        assert result is None
        mock_pool.aclose.assert_called_once()

    @patch("app.components.worker.pools.create_pool")
    async def test_get_load_test_result_validation_error_fallback(
        self, mock_create_pool
    ):
        """Test fallback when Pydantic validation fails."""
        # Clear cache to ensure fresh mock
        from app.components.worker.pools import clear_pool_cache

        await clear_pool_cache()

        # Create invalid data that will fail validation
        invalid_result_data = {
            "test_id": "test-123",
            "task_type": "io_simulation",
            "tasks_sent": -10,  # Invalid - negative value
            "tasks_completed": 20,  # Invalid - more than sent
            "total_duration_seconds": -5.0,  # Invalid - negative
            "batch_size": 10,
            "target_queue": "load_test",
        }

        arq_result = {"r": invalid_result_data}
        pickled_result = pickle.dumps(arq_result)

        mock_pool = AsyncMock()
        mock_pool.exists.return_value = True
        mock_pool.get.return_value = pickled_result
        mock_pool.ping.return_value = True  # For cache validation
        mock_pool.aclose.return_value = None  # Mock cleanup
        mock_create_pool.return_value = mock_pool

        # Should fall back to manual transformation when Pydantic validation fails
        result = await LoadTestService.get_load_test_result("test-123", "load_test")

        # Should still get a result (via fallback)
        assert result is not None
        mock_pool.aclose.assert_called_once()

    @patch("app.components.worker.pools.create_pool")
    async def test_get_load_test_result_exception_handling(self, mock_create_pool):
        """Test exception handling during result retrieval."""
        # Clear cache to ensure fresh mock
        from app.components.worker.pools import clear_pool_cache

        await clear_pool_cache()

        # Mock create_pool to raise exception
        mock_create_pool.side_effect = Exception("Redis connection lost")

        result = await LoadTestService.get_load_test_result("test-123", "load_test")

        assert result is None  # Should return None on exception
        # No aclose to assert since create_pool raised exception


class TestTransformOrchestratorResult:
    """Test the orchestrator result transformation logic."""

    def test_transform_complete_result(self):
        """Test transformation with all fields present."""
        orchestrator_result = {
            "test_id": "transform-test",
            "task_type": "memory_operations",
            "tasks_sent": 100,
            "tasks_completed": 95,
            "tasks_failed": 5,
            "total_duration_seconds": 45.5,
            "overall_throughput_per_second": 2.1,
            "failure_rate_percent": 5.0,
            "completion_percentage": 95.0,
            "average_throughput_per_second": 2.1,
            "monitor_duration_seconds": 45.0,
            "batch_size": 20,
            "delay_ms": 100,
            "target_queue": "system",
            "start_time": "2023-01-01T12:00:00",
            "end_time": "2023-01-01T12:00:45",
            "task_ids": ["id1", "id2", "id3"],
        }

        transformed = LoadTestService._transform_orchestrator_result(
            orchestrator_result
        )

        # Check basic structure
        assert transformed["task"] == "load_test_orchestrator"
        assert transformed["status"] == "completed"
        assert transformed["test_id"] == "transform-test"

        # Check configuration mapping
        config = transformed["configuration"]
        assert config["task_type"] == "memory_operations"
        assert config["num_tasks"] == 100
        assert config["batch_size"] == 20
        assert config["delay_ms"] == 100
        assert config["target_queue"] == "system"

        # Check metrics mapping
        metrics = transformed["metrics"]
        assert metrics["tasks_sent"] == 100
        assert metrics["tasks_completed"] == 95
        assert metrics["tasks_failed"] == 5
        assert metrics["total_duration_seconds"] == 45.5
        assert metrics["overall_throughput"] == 2.1
        assert metrics["failure_rate_percent"] == 5.0

        # Check optional fields
        assert transformed["start_time"] == "2023-01-01T12:00:00"
        assert transformed["end_time"] == "2023-01-01T12:00:45"
        assert transformed["task_ids"] == ["id1", "id2", "id3"]

    def test_transform_minimal_result(self):
        """Test transformation with minimal required fields."""
        minimal_result = {
            "test_id": "minimal",
            "task_type": "cpu_intensive",
            "tasks_sent": 10,
            "tasks_completed": 10,
            "total_duration_seconds": 5.0,
            "batch_size": 10,
            "target_queue": "load_test",
        }

        transformed = LoadTestService._transform_orchestrator_result(minimal_result)

        # Should handle missing optional fields gracefully
        assert transformed["test_id"] == "minimal"
        assert transformed["configuration"]["task_type"] == "cpu_intensive"
        assert transformed["metrics"]["tasks_sent"] == 10
        assert transformed["metrics"]["tasks_failed"] == 0  # Default
        assert transformed["metrics"]["overall_throughput"] == 0  # Default
        assert transformed["start_time"] is None
        assert transformed["task_ids"] == []


# Performance and stress tests
class TestLoadTestServicePerformance:
    """Test performance characteristics of the service."""

    def test_test_type_info_caching_behavior(self):
        """Test that test type info doesn't have unexpected side effects."""
        # Call multiple times to ensure no state leakage
        info1 = LoadTestService.get_test_type_info(LoadTestTypes.CPU_INTENSIVE)
        info2 = LoadTestService.get_test_type_info(LoadTestTypes.CPU_INTENSIVE)

        # Should return same data
        assert info1 == info2

        # Modifying one shouldn't affect the other (defensive copy)
        info1["name"] = "Modified"
        info3 = LoadTestService.get_test_type_info(LoadTestTypes.CPU_INTENSIVE)
        assert info3["name"] == "CPU Intensive"  # Should be unmodified

    def test_analysis_with_edge_case_values(self):
        """Test analysis functions with edge case values."""
        # Zero duration
        result_data = {
            "metrics": {
                "overall_throughput": 0.0,
                "tasks_sent": 0,
                "tasks_completed": 0,
                "total_duration_seconds": 0.0,
            }
        }

        analysis = LoadTestService._analyze_performance(result_data)
        assert analysis["throughput_rating"] == "poor"
        assert analysis["queue_pressure"] == "low"

        # Very high values
        result_data = {
            "metrics": {
                "overall_throughput": 10000.0,
                "tasks_sent": 100000,
                "tasks_completed": 100000,
                "total_duration_seconds": 10.0,
            }
        }

        analysis = LoadTestService._analyze_performance(result_data)
        assert analysis["throughput_rating"] == "excellent"
        assert analysis["efficiency_rating"] == "excellent"


# Error conditions and boundary testing
class TestLoadTestServiceErrorHandling:
    """Test error handling in LoadTestService."""

    @patch("app.core.config.get_load_test_queue")
    def test_analyze_load_test_result_missing_configuration(self, mock_get_queue):
        """Test analysis with missing configuration."""
        # Mock the queue function to return a valid default
        mock_get_queue.return_value = "load_test"

        incomplete_result = {
            "test_id": "incomplete",
            "status": "completed",
            # Missing configuration and metrics
        }

        # Should return a fallback LoadTestResult when validation fails
        result = LoadTestService._analyze_load_test_result(incomplete_result)
        assert isinstance(result, LoadTestResult)
        assert result.status == "failed"
        assert result.test_id == "incomplete"

    def test_validate_test_execution_with_edge_cases(self):
        """Test validation with edge case conditions."""
        result_data = {"status": "unknown"}
        test_info = {"validation_keys": ["some_key"]}

        validation = LoadTestService._validate_test_execution(result_data, test_info)

        assert validation["test_type_verified"] is False
        assert "unknown" in validation["issues"][0]

    def test_recommendations_empty_metrics(self):
        """Test recommendations generation with empty metrics."""
        empty_result = {"metrics": {}}

        recommendations = LoadTestService._generate_recommendations(empty_result)

        # Should handle missing metrics gracefully
        assert isinstance(recommendations, list)
        # Should still generate relevant recommendations based on defaults
        # (likely low throughput)
        assert len(recommendations) >= 1
