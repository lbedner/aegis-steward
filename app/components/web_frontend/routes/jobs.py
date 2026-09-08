"""Job progress for the browser (pattern 5).

The API streams a job's snapshots as JSON over ``/api/v1/jobs/{id}/events``.
This route follows the same job and re-emits each snapshot as a rendered
fragment, so an ``hx-ext="sse"`` element swaps HTML in and never needs a
line of JavaScript. Closes itself after the terminal frame, like the API.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from starlette.responses import StreamingResponse

from app.components.web_frontend.rendering import templates
from app.services.system.jobs import get_job_runner

router = APIRouter(prefix="/jobs")


def render_snapshot(request: Request, snapshot: dict[str, Any]) -> str:
    """One job snapshot as the fragment the follower swaps in."""
    template = templates.get_template("partials/jobs/status.html")
    return template.render(request=request, job=snapshot)


def sse_frame(event: str, html: str) -> str:
    """An SSE frame whose data is multi-line HTML (one ``data:`` per line)."""
    lines = "".join(f"data: {line}\n" for line in html.splitlines() if line.strip())
    return f"event: {event}\n{lines}\n"


@router.get("/{job_id}/events", include_in_schema=False)
async def job_events(request: Request, job_id: str) -> StreamingResponse:
    runner = get_job_runner()
    queue = await runner.subscribe_any(job_id)
    if queue is None:
        raise HTTPException(status_code=404, detail="Unknown job.")

    async def stream() -> AsyncIterator[str]:
        try:
            while True:
                snapshot = await queue.get()
                if snapshot is None:
                    break
                yield sse_frame("status", render_snapshot(request, snapshot))
                if snapshot["status"] != "running":
                    break
        except asyncio.CancelledError:
            raise
        finally:
            runner.unsubscribe(job_id, queue)

    return StreamingResponse(stream(), media_type="text/event-stream")
