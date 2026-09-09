"""Payee icons by URL.

The resolver (merchant_icon.py) stores every fetched favicon or logo in
``finance_icon``; this route hands one back by its key so a page can say
``<img src="/icons?key=starbucks.com">`` and the browser caches it once
across every table and swap, instead of each row carrying the bytes
inline. Only stored, positive rows are served: a page renders the
initial-letter fallback for anything else, so no image ever 404s in the
wild, and this route never fetches upstream.
"""

from __future__ import annotations

import base64

from fastapi import APIRouter, Depends, Query, Request
from starlette.responses import Response

from app.services.finance.deps import get_finance_service
from app.services.finance.domains.ledger import queries
from app.services.finance.domains.ledger.merchant_icon import ICON_PATH
from app.services.finance.service import FinanceService

router = APIRouter()

# An icon is refreshed at most once per negative-retry window, so a day of
# browser caching never shows a stale one for long; the ETag makes the
# revalidation a 304.
CACHE_CONTROL = "public, max-age=86400"


@router.get(ICON_PATH, include_in_schema=False)
async def icon(
    request: Request,
    key: str = Query(min_length=1, max_length=255),
    service: FinanceService = Depends(get_finance_service),
) -> Response:
    row = (await queries.icons_by_domains(service.db, [key])).get(key)
    if row is None or row.icon_b64 is None:
        return Response(status_code=404)
    etag = f'"{int(row.fetched_at.timestamp())}"'
    headers = {"Cache-Control": CACHE_CONTROL, "ETag": etag}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(
        content=base64.b64decode(row.icon_b64), media_type="image/png", headers=headers
    )
