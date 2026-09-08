"""Review: four queues as sibling routes under one sub-nav.

Approvals resolves pending changes (pattern 2) and re-sends the counts out
of band: the sub-nav here and the Overview's banner, wherever it happens
to be on the page. Uncategorized and No payee are the register with one
filter fixed, so every row action and the selection bar come for free;
Uncategorized adds the auto-categorize preview (rows out of band carrying
a suggestion nothing has written) and No payee the payee-group
assignment. Attention lists the new insights with a dismiss.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from starlette.responses import Response

from app.components.backend.api.finance.categories import list_category_options
from app.components.backend.api.finance.changes import (
    approve_batch,
    approve_change,
    list_changes,
    reject_batch,
    reject_change,
)
from app.components.backend.api.finance.insights import dismiss_insight, list_insights
from app.components.backend.api.finance.payees import (
    assign_payee_group,
    list_merchants,
    payee_groups,
)
from app.components.backend.api.finance.register import suggest_categories
from app.components.web_frontend.nav import section
from app.components.web_frontend.rendering import (
    close_dialog,
    navigate,
    render,
    templates,
    with_toast,
)
from app.components.web_frontend.routes.finance.register import (
    RegisterFilters,
    register_context,
    register_filters,
    rows_context,
    uncategorized_total,
)
from app.services.finance.deps import get_finance_service, get_owner_user_id
from app.services.finance.schemas import (
    BatchResolveRequest,
    PayeeGroupAssign,
    PendingChangeResponse,
    SuggestCategoriesRequest,
)
from app.services.finance.service import FinanceService

SECTION = section("review")
router = APIRouter(prefix=SECTION.path)

QUEUES: tuple[tuple[str, str, str], ...] = (
    ("approvals", "Approvals", ""),
    ("uncategorized", "Uncategorized", "/uncategorized"),
    ("no_payee", "No payee", "/no-payee"),
    ("attention", "Attention", "/attention"),
)
SEVERITY_TONE = {"critical": "error", "warning": "warn", "info": "muted"}
GROUP_COLUMNS = [
    {"key": "suggested_name", "label": "Suggested name"},
    {"key": "sample", "label": "Sample"},
    {"key": "count", "label": "Count", "kind": "int", "align": "right"},
    {"key": "total_amount", "label": "Total", "kind": "money", "align": "right"},
]


# --- counts and the sub-nav ---------------------------------------------


async def counts(service: FinanceService, owner_user_id: int | None) -> dict[str, int]:
    pending = await service.list_pending_changes(
        owner_user_id=owner_user_id, status="pending"
    )
    _rows, no_payee = await service.list_transactions(
        owner_user_id=owner_user_id, without_merchant=True, page_size=1
    )
    return {
        "approvals": len(pending),
        "uncategorized": await uncategorized_total(service, owner_user_id),
        "no_payee": no_payee,
        "attention": await service.count_new_insights(owner_user_id=owner_user_id),
    }


async def nav_context(
    service: FinanceService, owner_user_id: int | None, current: str
) -> dict[str, Any]:
    tally = await counts(service, owner_user_id)
    return {
        "review_nav": [
            {
                "key": key,
                "label": label,
                "href": SECTION.path + suffix,
                "count": tally[key],
            }
            for key, label, suffix in QUEUES
        ],
        "current_queue": current,
        "pending_count": tally["approvals"],
    }


async def _resolved(
    request: Request,
    service: FinanceService,
    owner_user_id: int | None,
    current: str,
    toast: str,
) -> Response:
    """A queue item's answer once it is gone: nothing in its place, the
    counts out of band, and a word about what happened."""
    await service.db.commit()
    response = templates.TemplateResponse(
        request=request,
        name="partials/review/resolved.html",
        context=await nav_context(service, owner_user_id, current),
    )
    return with_toast(response, toast)


# --- approvals ------------------------------------------------------------


def grouped(items: list[PendingChangeResponse]) -> tuple[list, list[dict[str, Any]]]:
    """Singles stay singles; a batch proposed in one go is one card."""
    singles = [c for c in items if not c.batch_id]
    batches: dict[str, list[PendingChangeResponse]] = {}
    for change in items:
        if change.batch_id:
            batches.setdefault(change.batch_id, []).append(change)
    return singles, [
        {"batch_id": batch_id, "title": rows[0].title, "items": rows}
        for batch_id, rows in batches.items()
    ]


@router.get("", include_in_schema=False)
async def approvals(
    request: Request,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    listing = await list_changes(
        status_filter="pending", service=service, owner_user_id=owner_user_id
    )
    singles, batches = grouped(listing.items)
    return render(
        request,
        "pages/review/approvals.html",
        {
            "section": SECTION,
            **await nav_context(service, owner_user_id, "approvals"),
            "singles": singles,
            "batches": batches,
        },
    )


@router.post("/changes/{change_id:int}/{verb}", include_in_schema=False)
async def resolve_change(
    request: Request,
    change_id: int,
    verb: str,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    handler = {"approve": approve_change, "reject": reject_change}.get(verb)
    if handler is None:
        raise HTTPException(status_code=404)
    change = await handler(change_id, service=service, owner_user_id=owner_user_id)
    word = "Approved" if verb == "approve" else "Rejected"
    return await _resolved(
        request, service, owner_user_id, "approvals", f"{word}: {change.title}."
    )


@router.post("/changes/batch/{batch_id}/{verb}", include_in_schema=False)
async def resolve_batch(
    request: Request,
    batch_id: str,
    verb: str,
    exclude_ids: Annotated[list[int], Form()] = [],
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    """Approve the batch minus the vetoed rows (they are rejected), or
    reject the whole batch."""
    if verb == "approve":
        summary = await approve_batch(
            batch_id,
            BatchResolveRequest(exclude_ids=exclude_ids),
            service=service,
            owner_user_id=owner_user_id,
        )
    elif verb == "reject":
        summary = await reject_batch(
            batch_id, service=service, owner_user_id=owner_user_id
        )
    else:
        raise HTTPException(status_code=404)
    parts = [f"{summary.approved} approved", f"{summary.rejected} rejected"]
    if summary.failed:
        parts.append(f"{summary.failed} failed")
    return await _resolved(
        request, service, owner_user_id, "approvals", ", ".join(parts) + "."
    )


# --- uncategorized and no payee: the register, one filter fixed -------------


async def _queue_page(
    request: Request,
    service: FinanceService,
    owner_user_id: int | None,
    key: str,
    filters: RegisterFilters,
    extra: dict[str, Any] | None = None,
) -> Response:
    href = next(SECTION.path + suffix for k, _label, suffix in QUEUES if k == key)
    accounts, _total = await service.list_accounts(
        owner_user_id=owner_user_id, page_size=500
    )
    register = await register_context(
        path=href,
        account=None,
        accounts=accounts,
        filters=filters,
        service=service,
        owner_user_id=owner_user_id,
    )
    return render(
        request,
        f"pages/review/{key}.html",
        {
            "section": SECTION,
            **await nav_context(service, owner_user_id, key),
            "register": register,
            **(extra or {}),
        },
    )


@router.get("/uncategorized", include_in_schema=False)
async def uncategorized(
    request: Request,
    filters: RegisterFilters = Depends(register_filters),
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    fixed = RegisterFilters(
        **{**filters.__dict__, "uncategorized": True, "category_id": None}
    )
    return await _queue_page(request, service, owner_user_id, "uncategorized", fixed)


@router.post("/uncategorized/suggest", include_in_schema=False)
async def suggest(
    request: Request,
    transaction_ids: Annotated[list[int], Form()] = [],
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    """The preview: every suggested row comes back out of band carrying
    its suggestion; nothing is written until a chip is accepted (which
    posts the ordinary categorize action)."""
    listing = await suggest_categories(
        SuggestCategoriesRequest(transaction_ids=transaction_ids or None),
        service=service,
        owner_user_id=owner_user_id,
    )
    if not listing.items:
        return with_toast(
            Response(status_code=200),
            "No suggestions: nothing here has a clear precedent yet.",
        )
    suggestions = {
        s.transaction_id: {
            "category_id": s.category_id,
            "category_name": s.category_name,
        }
        for s in listing.items
    }
    by_id = await service.transactions_by_ids(list(suggestions))
    context = await rows_context(
        service, list(by_id.values()), owner_user_id, suggestions
    )
    response = templates.TemplateResponse(
        request=request,
        name="partials/transactions/rows.html",
        context={**context, "show_account": True},
    )
    skipped = f", {listing.skipped} without one" if listing.skipped else ""
    return with_toast(
        response,
        f"{len(suggestions)} suggestion{'s' if len(suggestions) != 1 else ''}{skipped}. Nothing saved yet.",
    )


@router.get("/no-payee", include_in_schema=False)
async def no_payee(
    request: Request,
    view: str = "rows",
    filters: RegisterFilters = Depends(register_filters),
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    fixed = RegisterFilters(
        **{**filters.__dict__, "without_merchant": True, "merchant_id": None}
    )
    groups = None
    if view == "groups":
        groups = await payee_groups(
            limit=300, service=service, owner_user_id=owner_user_id
        )
    return await _queue_page(
        request,
        service,
        owner_user_id,
        "no_payee",
        fixed,
        {"view": view, "groups": groups, "group_columns": GROUP_COLUMNS},
    )


async def _assign_dialog(
    request: Request,
    service: FinanceService,
    owner_user_id: int | None,
    keys: list[str],
    errors: list[str],
    status_code: int = 200,
    **values: str,
) -> Response:
    merchants = await list_merchants(
        account_ids=None, service=service, owner_user_id=owner_user_id
    )
    categories = await list_category_options(service=service)
    return templates.TemplateResponse(
        request=request,
        name="partials/review/assign.html",
        context={
            "keys": keys,
            "merchants": merchants.items,
            "categories": categories.items,
            "errors": errors,
            "merchant_id": values.get("merchant_id", ""),
            "new_name": values.get("new_name", ""),
            "category_id": values.get("category_id", ""),
        },
        status_code=status_code,
    )


@router.get("/no-payee/assign", include_in_schema=False)
async def assign_form(
    request: Request,
    keys: list[str] = Query(default=[]),
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    if not keys:
        raise HTTPException(status_code=422, detail="Select at least one group.")
    return await _assign_dialog(request, service, owner_user_id, keys, [])


@router.post("/no-payee/assign", include_in_schema=False)
async def assign(
    request: Request,
    keys: Annotated[list[str], Form()] = [],
    merchant_id: Annotated[str, Form()] = "",
    new_name: Annotated[str, Form()] = "",
    category_id: Annotated[str, Form()] = "",
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    """Every transaction under the chosen keys gets the payee, existing or
    new; the groups view re-renders on the way back."""
    if not keys:
        raise HTTPException(status_code=422, detail="Select at least one group.")
    if not merchant_id and not new_name.strip():
        return await _assign_dialog(
            request,
            service,
            owner_user_id,
            keys,
            ["Pick a payee or name a new one."],
            422,
            merchant_id=merchant_id,
            new_name=new_name,
            category_id=category_id,
        )
    result = await assign_payee_group(
        PayeeGroupAssign(
            keys=keys,
            merchant_id=int(merchant_id) if merchant_id else None,
            name=new_name.strip() or None,
            category_id=int(category_id) if category_id else None,
        ),
        service=service,
        owner_user_id=owner_user_id,
    )
    await service.db.commit()
    response = Response(status_code=200)
    navigate(response, f"{SECTION.path}/no-payee?view=groups")
    return close_dialog(
        with_toast(response, f"Payee set on {result.updated} transactions.")
    )


# --- attention ------------------------------------------------------------


@router.get("/attention", include_in_schema=False)
async def attention(
    request: Request,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    listing = await list_insights(
        status_filter="new",
        insight_type=None,
        exclude_type=[],
        service=service,
        owner_user_id=owner_user_id,
    )
    return render(
        request,
        "pages/review/attention.html",
        {
            "section": SECTION,
            **await nav_context(service, owner_user_id, "attention"),
            "insights": [
                (i, SEVERITY_TONE.get(i.severity, "muted")) for i in listing.items
            ],
        },
    )


@router.post("/insights/{insight_id:int}/dismiss", include_in_schema=False)
async def dismiss(
    request: Request,
    insight_id: int,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    insight = await dismiss_insight(
        insight_id, service=service, owner_user_id=owner_user_id
    )
    return await _resolved(
        request, service, owner_user_id, "attention", f"Dismissed: {insight.title}."
    )
