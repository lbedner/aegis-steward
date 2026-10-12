"""
HTTP surface for the ephemeral request-metrics service.

Data lives in ``app.components.backend.middleware.performance.metrics_service``
— this file just exposes it. When the auth service is present the
router is admin-only (same pattern as worker / scheduler routes): the
data here (slowest routes, per-endpoint percentiles) is an operator
surface, not something regular users should be able to read. Auth-less
projects leave it open, matching ``/health/``.
"""

from typing import Any

from fastapi import APIRouter, Depends

from app.components.backend.middleware.performance import metrics_service
from app.services.auth.deps import get_current_active_user

# Operator-only metrics — RBAC stacks gate on ``role=admin`` (DB
# column as source of truth, matching auth/org modal pattern). Basic-
# auth stacks have no role concept, so fall back to "any authed user."
# Stored at module level so tests can override the closure via
# ``app.dependency_overrides`` — ``require_role`` returns a new closure
# per call, so a fresh ``require_role("admin")`` in test code wouldn't
# key against the one the router uses.
_require_admin = get_current_active_user

router = APIRouter(
    prefix="/metrics",
    tags=["metrics"],
    dependencies=[Depends(_require_admin)],
)


@router.get("/summary")
async def get_performance_summary() -> dict[str, Any]:
    """Cross-endpoint roll-up: total requests, avg, p95, slowest route."""
    return metrics_service.get_summary_stats()


@router.get("/endpoints")
async def get_endpoint_metrics() -> dict[str, dict[str, Any]]:
    """Per-endpoint counters + percentiles, keyed by ``"<METHOD> <pattern>"``."""
    return metrics_service.get_all_metrics()
