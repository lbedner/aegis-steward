"""Settings > Comms: what the scheduler sends, and where. The bill
reminder's preview (the job's own window and shaping, so the page cannot
drift from the mail) and this device's notifications."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlmodel.ext.asyncio.session import AsyncSession
from starlette.responses import Response

from app.components.web_frontend.nav import settings_nav as nav_context
from app.components.web_frontend.rendering import render, toast_response
from app.components.web_frontend.routes.finance.settings import SECTION
from app.core.config import settings
from app.core.db import get_async_db
from app.core.formatting import counted
from app.services.auth.deps import require_admin
from app.services.comms import push
from app.services.finance.deps import get_finance_service, get_owner_user_id
from app.services.finance.domains.planning.recurring.forecast import (
    reminder_subject,
    upcoming_outflows,
)
from app.services.finance.service import FinanceService

router = APIRouter(prefix=SECTION.path)


async def bill_email(
    service: FinanceService, owner_user_id: int | None
) -> dict[str, Any]:
    """What tomorrow's bill email would say. The same window and shaping
    the scheduled job uses, so the preview cannot drift from the mail."""
    days = settings.FINANCE_BILL_EMAIL_DAYS
    projection = await service.project_balances(owner_user_id=owner_user_id, days=days)
    bills = upcoming_outflows(projection)
    return {
        "to": settings.FINANCE_BILL_EMAIL_TO,
        "days": days,
        "bills": bills,
        "total": sum(b["amount"] for b in bills),
        "subject": reminder_subject(bills, days),
    }


@router.get("/comms", include_in_schema=False)
async def comms(
    request: Request,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    email = await bill_email(service, owner_user_id)
    return render(
        request,
        "pages/settings/comms.html",
        {
            "section": SECTION,
            **nav_context("comms"),
            "email": email,
            "push": await _push_device(request),
            "push_setting": push.VAPID_KEY,
        },
    )


async def _push_device(request: Request) -> dict[str, str] | None:
    """What the page's pushDevice needs: the key a device subscribes with
    and where it goes, every path the routes' own. None until a key can
    be kept."""
    try:
        key = await push.public_key()
    except push.PushConfigurationError:
        return None
    paths = {
        name: request.app.url_path_for(route)
        for name, route in (
            ("worker", "service_worker"),
            ("devices", "register_push_device"),
            ("test", "test_push"),
        )
    }
    return {"key": key, **paths}


@router.post(
    "/comms/test-push", include_in_schema=False, dependencies=[Depends(require_admin)]
)
async def test_push(
    request: Request, db: AsyncSession = Depends(get_async_db)
) -> Response:
    try:
        reached = await push.send_push(
            db,
            f"Test from {settings.PROJECT_DISPLAY_NAME}",
            "Notifications are on.",
            url=request.app.url_path_for("comms"),
        )
    except push.PushConfigurationError as exc:
        return toast_response(str(exc), "error")
    if not reached:
        return toast_response("No device has notifications on.", "warn")
    return toast_response(f"Sent to {counted(reached, 'device')}.")
