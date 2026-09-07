"""Operator surface for the traffic monitor ("who's hammering you").

Exposes the per-source request-volume aggregates the traffic middleware
collects. When the auth service is present the router is admin-only (same
pattern as the metrics route): source IPs are an operator surface, not
something regular users should read. Auth-less projects leave it open,
matching ``/health/``.
"""

from typing import Any

from fastapi import APIRouter, Query

from app.components.backend.middleware.traffic import traffic_monitor
from app.core.config import settings

router = APIRouter(
    prefix="/traffic",
    tags=["traffic"],
)


@router.get("/sources")
async def get_traffic_sources(
    window_hours: int | None = Query(default=None, ge=1, le=24),
    limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    """Top source IPs over the window, with a read-time dominance flag.

    ``window_hours`` defaults to ``TRAFFIC_WINDOW_HOURS`` and is capped at 24h
    (the hourly buckets' retention). The response carries ``backend``
    ("redis" or "memory") so the panel can note when counts are per-process
    and reset on restart.
    """
    return await traffic_monitor.snapshot(
        window_hours=window_hours or settings.TRAFFIC_WINDOW_HOURS,
        limit=limit,
        dominance_share=settings.TRAFFIC_DOMINANCE_SHARE,
        dominance_floor=settings.TRAFFIC_DOMINANCE_FLOOR,
    )
