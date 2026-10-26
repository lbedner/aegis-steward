"""Whether it is you, the bank, or Steward (#585).

When a balance is wrong the first question is whose fault it is, and the
answer was spread over the connection cards, Activity and Overseer. The
Status tab reads them together, kept apart by whose they are: a link that
needs you (the banner's list, with its fix), each provider by how its
syncs went over the last day, the bank sync by its last real run, and
Steward by its health tree. Derived from what the app already records,
never typed in, so it cannot say all is well when it is not.
"""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, Request
from sqlmodel import col, func, select
from starlette.responses import Response

from app.components.web_frontend.filters import freshness
from app.components.web_frontend.nav import section, settings_nav
from app.components.web_frontend.rendering import render
from app.components.web_frontend.routes.finance.bank_attention import links, needing
from app.core.config import settings
from app.core.time import utcnow
from app.services.finance.constants import PROVIDER_LABELS, sync_source
from app.services.finance.deps import get_finance_service, get_owner_user_id
from app.services.finance.service import FinanceImportBatch, FinanceService
from app.services.scheduler.models import JobExecution
from app.services.system.health import get_system_status

SECTION = section("settings")
router = APIRouter(prefix=SECTION.path)

SYNC_JOB = "finance_sync_connections"
WINDOW = timedelta(days=1)
_UNWELL = ("warn", "error")


def _row(key: str, name: str, words: str, tone: str) -> dict[str, str]:
    return {"key": key, "name": name, "words": words, "tone": tone}


async def _providers(
    service: FinanceService, providers: list[str]
) -> list[dict[str, str]]:
    """Each provider in use, by its share of failed syncs over the last
    day: the same runs Activity lists, counted."""
    sources = {sync_source(p): p for p in providers}
    counted = (
        await service.db.exec(
            select(
                FinanceImportBatch.source_type,
                FinanceImportBatch.status,
                func.count(),
            )
            .where(col(FinanceImportBatch.source_type).in_(sources))
            .where(FinanceImportBatch.started_at >= utcnow() - WINDOW)
            .group_by(FinanceImportBatch.source_type, FinanceImportBatch.status)
        )
    ).all()
    runs: dict[str, dict[str, int]] = {}
    for source, state, n in counted:
        runs.setdefault(sources[source], {})[state] = n
    rows = []
    for provider in providers:
        tally = runs.get(provider, {})
        total, failed = sum(tally.values()), tally.get("failed", 0)
        if not total:
            words, tone = "No syncs in the last day", "muted"
        elif not failed:
            words, tone = "Operational", "ok"
        elif failed == total:
            words, tone = "Down", "error"
        else:
            words, tone = (
                f"{round(100 * failed / total)}% of syncs failed in the last day",
                "warn",
            )
        rows.append(
            _row(provider, PROVIDER_LABELS.get(provider, provider), words, tone)
        )
    return rows


async def _sync(service: FinanceService) -> dict[str, str]:
    """The bank sync by its last real run. A dev stack never schedules it
    (``server_only``, #532), so there it is the server's."""
    name = "Bank sync"
    if settings.is_dev:
        return _row("sync", name, "Runs on the server", "muted")
    last = (
        await service.db.exec(
            select(JobExecution)
            .where(JobExecution.job_id == SYNC_JOB)
            .order_by(col(JobExecution.started_at).desc())
            .limit(1)
        )
    ).first()
    if last is None:
        return _row("sync", name, "Not run yet", "muted")
    when = freshness(last.started_at, "sync")["label"]
    words, tone = {
        "success": (f"Ran {when}", "ok"),
        "failed": (f"Failed {when}", "error"),
        "missed": (f"Missed {when}", "warn"),
    }.get(last.status, ("Running now", "muted"))
    return _row("sync", name, words, tone)


async def _steward() -> dict[str, str]:
    """Steward itself, by the health tree Overseer draws: what is unwell,
    by name. A sick part sickens every part above it, so only the
    deepest are named ("worker", not "aegis.components.worker")."""
    status = await get_system_status()
    if status.overall_healthy:
        return _row("steward", "Steward", "Operational", "ok")
    sick = status.unhealthy_components
    named = sorted(
        {
            n.rsplit(".", 1)[-1]
            for n in sick
            if not any(o.startswith(n + ".") for o in sick)
        }
    )
    return _row("steward", "Steward", "Degraded: " + ", ".join(named), "warn")


@router.get("/status", include_in_schema=False)
async def status(
    request: Request,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    """You, the bank, or Steward: what needs you first, then each service.

    The health tree is walked FIRST. This page's session begins IMMEDIATE
    at its first read and so holds the write lock from then on; every
    health check opens a session of its own, and walked after that read
    each waited out the timeout behind the page that waited on it."""
    steward = await _steward()
    found = await links(service, owner_user_id)
    needs = needing(found)
    services = [
        steward,
        await _sync(service),
        *await _providers(service, sorted({c.provider for c in found})),
    ]
    if any(row["tone"] in _UNWELL for row in services):
        headline, tone = "Degraded", "warn"
    elif needs:
        headline, tone = "Needs you", "error"
    else:
        headline, tone = "All good", "ok"
    return render(
        request,
        "pages/settings/status.html",
        {
            "section": SECTION,
            **settings_nav("status"),
            "headline": headline,
            "tone": tone,
            "needs": needs,
            "services": services,
            "path": SECTION.path,
        },
    )
