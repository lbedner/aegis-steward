"""Routes that are not a section: today only the root redirect.

Section pages live under ``routes/finance/``, one module per sidebar entry.
"""

from fastapi import APIRouter
from fastapi.responses import RedirectResponse

router = APIRouter()


@router.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    """There is no landing page; the app opens on Overview."""
    return RedirectResponse(url="/overview", status_code=303)
