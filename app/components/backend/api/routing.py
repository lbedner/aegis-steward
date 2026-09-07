from fastapi import FastAPI

from app.components.backend.api import events as worker_events
from app.components.backend.api import (
    health,
    jobs,
    load_test_api,
    metrics,
    pastebox,
    scheduler,
    task_history,
    traffic,
    worker,
)
from app.components.backend.api.ai.router import router as ai_router
from app.components.backend.api.comms.router import router as comms_router
from app.components.backend.api.finance.router import router as finance_router
from app.components.backend.api.llm.router import router as llm_router


def include_routers(app: FastAPI) -> None:
    """Include all API routers in the FastAPI app"""
    app.include_router(health.router, prefix="/health", tags=["health"])
    app.include_router(jobs.router, prefix="/api/v1")
    app.include_router(metrics.router, prefix="/api/v1")
    app.include_router(pastebox.router, prefix="/api/v1")
    app.include_router(traffic.router, prefix="/api/v1")
    app.include_router(load_test_api.router, prefix="/api/v1")
    app.include_router(worker.router, prefix="/api/v1")
    app.include_router(task_history.router, prefix="/api/v1")
    app.include_router(worker_events.router, prefix="/events", tags=["events"])
    app.include_router(scheduler.router, prefix="/api/v1")
    app.include_router(ai_router, prefix="/api/v1")
    app.include_router(llm_router, prefix="/api/v1")
    app.include_router(comms_router, prefix="/api/v1")
    app.include_router(finance_router, prefix="/api/v1")
