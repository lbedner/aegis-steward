"""Pydantic models shared between worker and HTTP load-test services.

Subclasses extend the base configuration/metrics with their own fields
(worker adds task type, batch size, queue; HTTP adds method, path, latency
percentiles, status code distribution). The base classes guarantee
identical validation semantics for shared fields, so consumers can rely on
the same invariants regardless of which load-test variant produced a
result.
"""

from typing import Literal

from pydantic import BaseModel, Field, ValidationInfo, field_validator, model_validator


class BaseLoadTestConfiguration(BaseModel):
    """Minimal configuration envelope.

    Subclasses add their own fields (target identifier, concurrency, etc.).
    The base only carries ``test_id`` because that is the single piece of
    metadata every load test produces regardless of variant.
    """

    test_id: str | None = Field(default=None, description="Unique test identifier")


class BaseLoadTestMetrics(BaseModel):
    """Metrics fields common to every load-test variant.

    Cross-field validators enforce internal consistency: completed/failed
    counts cannot exceed sent, and the declared failure-rate percentage
    must agree with the count ratio (within 0.1 percentage points to
    tolerate float rounding from upstream serialization).
    """

    tasks_sent: int = Field(..., ge=0, description="Total tasks/requests issued")
    tasks_completed: int = Field(
        ..., ge=0, description="Successfully completed tasks/requests"
    )
    tasks_failed: int = Field(default=0, ge=0, description="Failed tasks/requests")
    total_duration_seconds: float = Field(..., ge=0, description="Total duration")
    overall_throughput: float = Field(
        default=0, ge=0, description="Overall throughput (units/sec)"
    )
    failure_rate_percent: float = Field(
        default=0, ge=0, le=100, description="Failure rate percentage"
    )
    completion_percentage: float = Field(
        default=0, ge=0, le=100, description="Completion percentage"
    )

    @field_validator("tasks_completed")
    @classmethod
    def _completed_not_exceed_sent(cls, v: int, info: ValidationInfo) -> int:
        sent = info.data.get("tasks_sent") if info.data else None
        if sent is not None and v > sent:
            raise ValueError(f"Completed tasks ({v}) cannot exceed sent tasks ({sent})")
        return v

    @field_validator("tasks_failed")
    @classmethod
    def _failed_not_exceed_sent(cls, v: int, info: ValidationInfo) -> int:
        sent = info.data.get("tasks_sent") if info.data else None
        if sent is not None and v > sent:
            raise ValueError(f"Failed tasks ({v}) cannot exceed sent tasks ({sent})")
        return v

    @field_validator("failure_rate_percent")
    @classmethod
    def _failure_rate_consistent(cls, v: float, info: ValidationInfo) -> float:
        if not info.data:
            return v
        sent = info.data.get("tasks_sent")
        failed = info.data.get("tasks_failed")
        if sent is None or failed is None or sent == 0:
            return v
        expected = (failed / sent) * 100
        if abs(v - expected) > 0.1:
            raise ValueError(
                f"failure rate {v}% does not match counts "
                f"({failed}/{sent} = {expected:.1f}%)"
            )
        return v


class PerformanceAnalysisBase(BaseModel):
    """The two ratings every load test produces.

    Variants extend this with a third dimension (worker: queue_pressure;
    HTTP: latency_rating). The base captures the shared semantics so
    consumers can switch on ``throughput_rating`` / ``efficiency_rating``
    without caring which variant produced the result.
    """

    throughput_rating: Literal["unknown", "poor", "fair", "good", "excellent"]
    efficiency_rating: Literal["unknown", "poor", "fair", "good", "excellent"]


class ValidationStatus(BaseModel):
    """Whether a test executed as expected. Identical for every variant."""

    test_type_verified: bool = False
    expected_metrics_present: bool = False
    performance_signature_match: Literal["unknown", "verified", "partial", "failed"] = (
        "unknown"
    )
    issues: list[str] = Field(default_factory=list)


class LoadTestAnalysis(BaseModel):
    """Analysis envelope attached to a result.

    Subclasses can override ``performance_analysis`` to use a variant-specific
    subtype of ``PerformanceAnalysisBase``, or add variant-specific fields
    (e.g. worker's ``test_type_info``).
    """

    performance_analysis: PerformanceAnalysisBase
    validation_status: ValidationStatus
    recommendations: list[str] = Field(default_factory=list)


class BaseLoadTestResult(BaseModel):
    """Result envelope.

    Subclasses tighten the ``configuration`` and ``metrics`` field types to
    their own variant-specific models. Status/error consistency is enforced
    by a model-level validator: ``failed`` status without an ``error``
    message indicates a programming bug and is rejected.
    """

    status: Literal["completed", "failed", "timed_out"]
    test_id: str
    configuration: BaseLoadTestConfiguration
    metrics: BaseLoadTestMetrics
    start_time: str | None = None
    end_time: str | None = None
    error: str | None = None
    analysis: LoadTestAnalysis | None = None

    @model_validator(mode="after")
    def _status_error_consistent(self) -> BaseLoadTestResult:
        if self.status == "failed" and not self.error:
            raise ValueError("Failed status requires error message")
        return self
