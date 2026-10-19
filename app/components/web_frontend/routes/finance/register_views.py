"""Saved register views (#326): a register filter set kept under a name.

A view is the register's own URL, so saving one reads the page's current
address (``HX-Current-URL``, which holds every filter) and opening one is
a link. The menu loads by itself when the register first draws
(``hx_load``) and keeps across the register's swaps, so a filter change
costs no read for it.
"""

from __future__ import annotations

from typing import Annotated
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Form, Header, Request
from starlette.responses import Response

from app.components.web_frontend.nav import section
from app.components.web_frontend.rendering import templates
from app.services.finance.deps import get_finance_service, get_owner_user_id
from app.services.finance.domains.ledger import views
from app.services.finance.service import FinanceService

SECTION = section("accounts")
router = APIRouter(prefix=f"{SECTION.path}/views")


async def _menu(
    request: Request,
    service: FinanceService,
    owner_user_id: int | None,
    errors: list[str] | None = None,
) -> Response:
    return templates.TemplateResponse(
        request=request,
        name="partials/accounts/views.html",
        context={
            "views": await views.list_views(service.db, owner_user_id),
            "errors": errors or [],
        },
        status_code=422 if errors else 200,
    )


@router.get("", include_in_schema=False)
async def menu(
    request: Request,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    return await _menu(request, service, owner_user_id)


@router.post("", include_in_schema=False)
async def save(
    request: Request,
    name: Annotated[str, Form()] = "",
    hx_current_url: Annotated[str | None, Header()] = None,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    """Keep the register as it is now under ``name``."""
    here = urlsplit(hx_current_url or "")
    if not name.strip():
        return await _menu(request, service, owner_user_id, ["Name the view."])
    if not here.path.startswith(SECTION.path):
        return await _menu(
            request, service, owner_user_id, ["Save it from a register."]
        )
    url = here.path + (f"?{here.query}" if here.query else "")
    await views.save_view(service.db, owner_user_id, name, url)
    await service.db.commit()
    return await _menu(request, service, owner_user_id)


@router.delete("/{view_id:int}", include_in_schema=False)
async def remove(
    request: Request,
    view_id: int,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    await views.delete_view(service.db, owner_user_id, view_id)
    await service.db.commit()
    return await _menu(request, service, owner_user_id)
