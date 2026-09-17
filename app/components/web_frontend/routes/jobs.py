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

from fastapi import APIRouter, Request
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


# What a follower is told about a job the app no longer has. Jobs run
# IN the web process, so a restart takes the running ones with it - and
# a 404 on the stream is invisible to the SSE extension: no frame ever
# arrives, the spinner turns forever, and the reader is left believing
# an import is still going. One terminal frame is the honest answer, and
# it carries the retry marker so the file picker comes back.
LOST = {
    "id": "",
    "name": "",
    "label": None,
    "status": "lost",
    "error": (
        "That run is gone - the app restarted while it was going. "
        "Nothing was half-imported: a run that does not finish writes "
        "nothing. Pick the file again."
    ),
    "result": None,
}


@router.get("/{job_id}/events", include_in_schema=False)
async def job_events(request: Request, job_id: str) -> StreamingResponse:
    runner = get_job_runner()
    queue = await runner.subscribe_any(job_id)
    if queue is None:
        lost = sse_frame("status", render_snapshot(request, {**LOST, "id": job_id}))
        return StreamingResponse(iter([lost]), media_type="text/event-stream")

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
