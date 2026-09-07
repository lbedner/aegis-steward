"""Full-page routes for the web frontend.

One handler per page: build the context, hand it to a template. Anything a
page swaps in without a reload belongs in ``routes/partials/`` instead.
"""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.components.web_frontend.main import templates

router = APIRouter()


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def landing(request: Request) -> HTMLResponse:
    """Landing page."""
    return templates.TemplateResponse(request=request, name="pages/landing.html")
