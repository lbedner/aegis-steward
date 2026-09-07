"""Shared analysis helpers: rating buckets and recommendation generation.

The worker and HTTP services have wildly different performance scales
(worker tasks: dozens/sec is excellent; HTTP requests: thousands/sec is
expected), so the helpers here are parametrized by threshold dataclasses.
Each service passes its own tuned thresholds; the bucketing logic and
recommendation messages stay shared.
"""

from dataclasses import dataclass
from typing import Literal

Rating = Literal["poor", "fair", "good", "excellent"]


@dataclass(frozen=True)
class RatingBands:
    """Threshold bands for ``rate_bucket``.

    For higher-is-better metrics (throughput, success rate), ``excellent``
    is the highest threshold and values at or above it earn "excellent".
    For lower-is-better metrics (latency p95), ``excellent`` is the lowest
    threshold and values at or below it earn "excellent". The
    ``higher_is_better`` flag selects between the two semantics.

    Boundary values always fall into the BETTER tier (>= for higher-is-better,
    <= for lower-is-better), so a value exactly at the threshold doesn't
    fall through to the worse rating.
    """

    excellent: float
    good: float
    fair: float
    higher_is_better: bool = True


def rate_bucket(value: float, bands: RatingBands) -> Rating:
    """Bucket ``value`` into one of four ratings using the given bands."""
    if bands.higher_is_better:
        if value >= bands.excellent:
            return "excellent"
        if value >= bands.good:
            return "good"
        if value >= bands.fair:
            return "fair"
        return "poor"
    # Lower is better
    if value <= bands.excellent:
        return "excellent"
    if value <= bands.good:
        return "good"
    if value <= bands.fair:
        return "fair"
    return "poor"


@dataclass(frozen=True)
class RecommendationThresholds:
    """Thresholds that drive ``build_recommendations``.

    Each service supplies its own tuned values. Worker defaults are
    throughput >= 10/sec, failure rate <= 5%, duration <= 60s for >= 200
    tasks. HTTP defaults are an order of magnitude higher on throughput
    and stricter on failure rate.
    """

    low_throughput: float
    high_failure_rate: float
    long_duration: float
    small_task_count: int


def build_recommendations(
    throughput: float,
    failure_rate: float,
    duration: float,
    tasks_sent: int,
    thresholds: RecommendationThresholds,
) -> list[str]:
    """Generate human-readable recommendations from metrics.

    Returns an empty list when nothing is wrong. The saturation
    recommendation requires BOTH a long duration AND a small task count;
    long duration alone with many tasks is just a long-running test, not
    a saturation signal.
    """
    recommendations: list[str] = []

    if throughput < thresholds.low_throughput:
        recommendations.append(
            "Low throughput detected. Consider reducing task complexity "
            "or increasing concurrency."
        )

    if failure_rate > thresholds.high_failure_rate:
        recommendations.append(
            f"High failure rate ({failure_rate:.1f}%). Check logs for error patterns."
        )

    if duration > thresholds.long_duration and tasks_sent < thresholds.small_task_count:
        recommendations.append(
            "Long execution time for relatively few tasks suggests "
            "saturation. Consider testing with smaller batches or "
            "different settings."
        )

    return recommendations
