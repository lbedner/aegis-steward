"""Say what an anomaly was, from the Attention tab (FW-09).

The assistant proposes a resolution through the queue; a person reading
the Attention list records one here, directly - their own reading needs
no second approval. Both land in ``resolve_insight``, so a resolution
means one thing wherever it was made.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from starlette.responses import Response

from app.components.web_frontend.nav import section
from app.components.web_frontend.rendering import (
    dialog,
    dialog_done,
    or_404,
    where_from,
)
from app.services.finance.deps import get_finance_service, get_owner_user_id
from app.services.finance.domains.planning import queries
from app.services.finance.domains.planning.insights import resolve_insight
from app.services.finance.models.planning import INSIGHT_RESOLUTIONS
from app.services.finance.service import FinanceService

SECTION = section("review")
router = APIRouter(prefix=SECTION.path)


def _resolve_dialog(
    request: Request,
    insight: object,
    state: str | None,
    note: str | None,
    status_code: int = 200,
    errors: list[str] | None = None,
) -> Response:
    return dialog(
        request,
        "partials/review/resolve.html",
        status_code,
        post=f"{SECTION.path}/insights/{insight.id}/resolve",  # type: ignore[attr-defined]
        insight=insight,
        states=INSIGHT_RESOLUTIONS,
        state=state,
        note=note,
        errors=errors or [],
    )


@router.get("/insights/{insight_id:int}/resolve", include_in_schema=False)
async def resolve_dialog(
    request: Request,
    insight_id: int,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    insight = or_404(
        await queries.insight_by_id(service.db, insight_id, owner_user_id=owner_user_id)
    )
    return _resolve_dialog(
        request, insight, insight.resolution, insight.resolution_note
    )


@router.post("/insights/{insight_id:int}/resolve", include_in_schema=False)
async def save_resolution(
    request: Request,
    insight_id: int,
    state: Annotated[str, Form()] = "",
    note: Annotated[str, Form()] = "",
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    insight = or_404(
        await queries.insight_by_id(service.db, insight_id, owner_user_id=owner_user_id)
    )
    try:
        await resolve_insight(
            service.db, insight_id, state=state, note=note, owner_user_id=owner_user_id
        )
    except ValueError as exc:
        return _resolve_dialog(request, insight, state, note, 422, [str(exc)])
    await service.db.commit()
    return dialog_done(where_from(request, f"{SECTION.path}/attention"), "Saved")
