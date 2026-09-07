"""The projected section."""

from fastapi import APIRouter, Request
from starlette.responses import Response

from app.components.web_frontend.main import render
from app.components.web_frontend.nav import section

SECTION = section("projected")
router = APIRouter()


@router.get(SECTION.path, include_in_schema=False)
async def page(request: Request) -> Response:
    return render(request, "pages/projected.html", {"section": SECTION})
