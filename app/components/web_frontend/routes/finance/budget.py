"""The budget section."""

from fastapi import APIRouter, Request
from starlette.responses import Response

from app.components.web_frontend.nav import section
from app.components.web_frontend.rendering import render

SECTION = section("budget")
router = APIRouter()


@router.get(SECTION.path, include_in_schema=False)
async def page(request: Request) -> Response:
    return render(request, "pages/budget.html", {"section": SECTION})
