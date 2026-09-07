"""Simple system maintenance tasks for the system worker."""

import asyncio
from datetime import UTC, datetime
from typing import Any

from app.core.log import logger


async def system_health_check(ctx: dict[str, Any]) -> dict[str, str]:
    """Verify worker connectivity and responsiveness.

    Returns a timestamped health status to confirm the worker process
    is alive and can execute tasks. Used by the scheduler for periodic
    liveness monitoring.
    """
    logger.debug("Running system health check task")

    # Simple health check - just return current timestamp
    return {
        "status": "healthy",
        "timestamp": datetime.now(UTC).isoformat(),
        "task": "system_health_check",
    }


async def cleanup_temp_files(ctx: dict[str, Any]) -> dict[str, str]:
    """Remove stale temporary files from the working directory.

    Placeholder for application-specific cleanup logic. Scans for
    expired temp files, upload artifacts, and cache entries.
    """
    logger.info("Running temp file cleanup task")

    # Placeholder for actual cleanup logic
    await asyncio.sleep(0.1)  # Simulate some work

    return {
        "status": "completed",
        "timestamp": datetime.now(UTC).isoformat(),
        "task": "cleanup_temp_files",
    }
