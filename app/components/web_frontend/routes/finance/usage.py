"""Settings > Usage (#270): what models and voice cost.

One page over the one ledger: chat and agent turns, live calls, hearing
and speaking, by kind and by model, and each live call as its minutes
plus the answers given during it.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlmodel.ext.asyncio.session import AsyncSession
from starlette.responses import Response

from app.components.web_frontend import ranges
from app.components.web_frontend.filters import short_date, usd
from app.components.web_frontend.nav import section, settings_nav
from app.components.web_frontend.rendering import render
from app.core.db import get_async_db
from app.services.ai.usage_report import usage_report

router = APIRouter()

SECTION = section("settings")
USAGE = SECTION.path + "/usage"
DEFAULT_DAYS = 30
# "All" reads back ten years, the ceiling the API's windows use.
_ALL_DAYS = 3650
WINDOWS = tuple(w for w in ranges.WINDOWS if w[0] != 1)

MODEL_COLUMNS = [
    {"key": "kind", "label": "Kind"},
    {"key": "model", "label": "Model"},
    {"key": "uses", "label": "Uses", "align": "right"},
    {"key": "minutes", "label": "Minutes", "align": "right"},
    {"key": "tokens", "label": "Tokens", "align": "right"},
    {"key": "cost", "label": "Cost", "align": "right"},
]
CALL_COLUMNS = [
    {"key": "when", "label": "When"},
    {"key": "minutes", "label": "Minutes", "align": "right"},
    {"key": "call_cost", "label": "Call", "align": "right"},
    {"key": "answers_cost", "label": "Her answers", "align": "right"},
    {"key": "total", "label": "Total", "align": "right"},
    {"key": "ended", "label": "Ended"},
]


@router.get(USAGE, include_in_schema=False)
async def usage(
    request: Request,
    days: int = DEFAULT_DAYS,
    db: AsyncSession = Depends(get_async_db),
) -> Response:
    report = await usage_report(db, days=_ALL_DAYS if days == ranges.ALL else days)
    return render(
        request,
        "pages/settings/usage.html",
        {
            "section": SECTION,
            **settings_nav("usage"),
            "path": USAGE,
            "days": days,
            "ranges": WINDOWS,
            "total": usd(report["total"]),
            "kinds": [
                {
                    "label": kind["label"],
                    "value": usd(kind["cost"]),
                    "caption": (
                        f"{kind['minutes']:.1f} min · {kind['uses']} uses"
                        if kind["minutes"]
                        else f"{kind['uses']} uses"
                    ),
                }
                for kind in report["kinds"]
            ],
            "model_columns": MODEL_COLUMNS,
            "models": [
                {
                    **m,
                    "minutes": f"{m['minutes']:.1f}" if m["minutes"] else "",
                    "tokens": f"{m['tokens']:,}" if m["tokens"] else "",
                    "cost": usd(m["cost"]),
                }
                for m in report["models"]
            ],
            "call_columns": CALL_COLUMNS,
            "calls": [
                {
                    "when": short_date(call["when"]),
                    "minutes": f"{call['minutes']:.1f}",
                    "call_cost": usd(call["call_cost"]),
                    "answers_cost": usd(call["answers_cost"]),
                    "total": usd(call["total"]),
                    "ended": call["ended"],
                }
                for call in report["calls"]
            ],
        },
    )
