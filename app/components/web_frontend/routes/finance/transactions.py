"""Actions on transactions (pattern 2), single or in bulk.

Each action changes the given transactions and answers with every
touched row out of band plus the counters that moved (the uncategorised
count). The trigger swaps nothing itself, so a row's own menu and the
selection bar share one contract. Dialog forms (tag, payee) re-render
with a 422 on bad input and close on success.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from starlette.responses import Response

from app.components.backend.api.finance.categories import list_category_options
from app.components.backend.api.finance.declare import (
    declare_recurring_transactions,
    preview_declare_recurring,
)
from app.components.backend.api.finance.payees import (
    assign_merchant,
    create_merchant,
    list_merchants,
    merchant_category_summary,
)
from app.components.backend.api.finance.register import (
    hydrate_transactions,
    list_tags,
    similar_transactions,
    split_transaction,
    unsplit_transaction,
)
from app.components.web_frontend.filters import money_to_cents
from app.components.web_frontend.rendering import close_dialog, templates, with_toast
from app.components.web_frontend.routes.finance.register import (
    payee_label,
    rows_context,
    uncategorized_total,
)
from app.services.finance.deps import get_finance_service, get_owner_user_id
from app.services.finance.models import FinanceTransaction
from app.services.finance.schemas import (
    DeclareRecurring,
    MerchantAssign,
    MerchantCreate,
    SplitPart,
    TransactionSplitRequest,
)
from app.services.finance.service import FinanceService

router = APIRouter(prefix="/transactions")


async def _txns(
    service: FinanceService, ids: list[int], owner_user_id: int | None
) -> list[FinanceTransaction]:
    if not ids:
        raise HTTPException(status_code=422, detail="Select at least one transaction.")
    by_id = await service.transactions_by_ids(list(set(ids)))
    rows = [by_id[i] for i in ids if i in by_id]
    if len(rows) != len(set(ids)) or any(
        owner_user_id is not None and r.owner_user_id != owner_user_id for r in rows
    ):
        raise HTTPException(status_code=404)
    return rows


async def _title(
    service: FinanceService, txns: list[FinanceTransaction], owner_user_id: int | None
) -> str:
    """What to call the selection: one transaction by whatever it is
    called now (see ``payee_label``), several by their count."""
    if len(txns) != 1:
        return f"{len(txns)} transactions"
    txn = txns[0]
    names = await service.merchant_names({txn.merchant_id}) if txn.merchant_id else {}
    return f'"{payee_label(names.get(txn.merchant_id), txn.merchant_name, txn.name)}"'


def _shared(values: list[set[Any]]) -> set[Any]:
    """What every selected row already carries. The picker disables these,
    because choosing one would write nothing, and a step that does
    nothing is a step to undo."""
    return set.intersection(*values) if values else set()


def _shared_merchant(txns: list[FinanceTransaction]) -> set[int]:
    return _shared([{txn.merchant_id} for txn in txns]) - {None}


async def _shared_tags(
    service: FinanceService, txns: list[FinanceTransaction]
) -> set[str]:
    """The tags every selected row already wears, through the one
    hydration the register uses."""
    items = await hydrate_transactions(service, txns)
    return _shared([{tag.name for tag in (item.tags or [])} for item in items])


async def _rows_response(
    request: Request,
    service: FinanceService,
    txns: list[FinanceTransaction],
    owner_user_id: int | None,
    show_account: bool,
    name: str = "partials/transactions/rows.html",
    extra: dict[str, Any] | None = None,
) -> Response:
    context = await rows_context(service, txns, owner_user_id)
    return templates.TemplateResponse(
        request=request,
        name=name,
        context={**context, "show_account": show_account, **(extra or {})},
    )


@router.post("/{transaction_id}/categorize", include_in_schema=False)
async def categorize(
    request: Request,
    transaction_id: int,
    category_id: Annotated[str, Form()] = "",
    show_account: Annotated[bool, Form()] = False,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    """Set (or clear, with a blank value) the category from the row's
    select. Swaps the row itself (closest tr), so the answer is the row."""
    await _txns(service, [transaction_id], owner_user_id)
    txn = await service.categorize_transaction(
        transaction_id,
        int(category_id) if category_id else None,
        owner_user_id=owner_user_id,
        source="user",
    )
    assert txn is not None
    await service.db.commit()
    context = await rows_context(service, [txn], owner_user_id)
    return templates.TemplateResponse(
        request=request,
        name="partials/transactions/rows.html",
        context={**context, "show_account": show_account, "oob_rows": False},
    )


def picker_options(rows: list[Any], key: str = "id") -> list[Any]:
    """Rows shaped for the ``picker`` macro: what to submit, what to read,
    and how often it is used. One shaping, so the payee list and the tag
    list cannot start counting differently. Whole, because the browser
    does the narrowing."""
    return [
        SimpleNamespace(
            id=getattr(row, key),
            name=row.name,
            fact=f"{row.transaction_count:,}" if row.transaction_count else "",
        )
        for row in rows
    ]


async def _payee_options(
    service: FinanceService, owner_user_id: int | None
) -> list[Any]:
    """Payees for the picker, the ones you use most first, then the rest
    alphabetically — a search is for the tail, not the top."""
    listing = await list_merchants(
        account_ids=None, service=service, owner_user_id=owner_user_id
    )
    ranked = sorted(
        listing.items, key=lambda m: (-m.transaction_count, m.name.casefold())
    )
    return picker_options(ranked)


@router.get("/tag", include_in_schema=False)
async def tag_form(
    request: Request,
    transaction_ids: list[int] = Query(default=[]),
    show_account: bool = False,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    txns = await _txns(service, transaction_ids, owner_user_id)
    return await _tag_dialog(request, service, owner_user_id, txns, show_account)


async def _tag_dialog(
    request: Request,
    service: FinanceService,
    owner_user_id: int | None,
    txns: list[FinanceTransaction],
    show_account: bool,
    errors: list[str] | None = None,
    status_code: int = 200,
) -> Response:
    """The same picker the payee dialog uses: a tag is whatever you name
    in the moment, so picking one and typing its near-miss both land
    here (the service resolves a tag by normalized name)."""
    tags = await list_tags(service=service, owner_user_id=owner_user_id)
    return templates.TemplateResponse(
        request=request,
        name="partials/transactions/tag.html",
        context={
            "title": await _title(service, txns, owner_user_id),
            "transaction_ids": [t.id for t in txns],
            "show_account": show_account,
            # Keyed by name: naming a tag and picking one are the same
            # write, since the service resolves by normalized name.
            "tags": picker_options(tags, key="name"),
            "current": await _shared_tags(service, txns),
            "errors": errors or [],
        },
        status_code=status_code,
    )


@router.post("/tag", include_in_schema=False)
async def tag(
    request: Request,
    transaction_ids: Annotated[list[int], Form()] = [],
    name: Annotated[str, Form()] = "",
    tag_id: Annotated[str, Form()] = "",
    show_account: Annotated[bool, Form()] = False,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    """Put a tag on the selection. Picking an existing tag and naming a
    new one are the same write: the service resolves by normalized name,
    so a near-miss lands on the tag it nearly missed."""
    txns = await _txns(service, transaction_ids, owner_user_id)
    label = (tag_id or name).strip()
    if not label:
        return await _tag_dialog(
            request,
            service,
            owner_user_id,
            txns,
            show_account,
            errors=["Pick a tag or name a new one."],
            status_code=422,
        )
    await service.tag_transactions(transaction_ids, label, owner_user_id=owner_user_id)
    await service.db.commit()
    response = await _rows_response(request, service, txns, owner_user_id, show_account)
    return close_dialog(with_toast(response, f"Tagged {len(txns)} as {label}"))


@router.delete("/{transaction_id}/tags/{tag_id}", include_in_schema=False)
async def untag(
    request: Request,
    transaction_id: int,
    tag_id: int,
    show_account: bool = False,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    txns = await _txns(service, [transaction_id], owner_user_id)
    await service.untag_transactions(
        [transaction_id], tag_id, owner_user_id=owner_user_id
    )
    await service.db.commit()
    return await _rows_response(
        request, service, txns, owner_user_id, show_account, extra={"oob_rows": False}
    )


@router.get("/payee", include_in_schema=False)
async def payee_form(
    request: Request,
    transaction_ids: list[int] = Query(default=[]),
    show_account: bool = False,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    """The picker. The same route answers the search, so the list is
    filtered where the whole list lives."""
    txns = await _txns(service, transaction_ids, owner_user_id)
    return await _payee_dialog(request, service, owner_user_id, txns, show_account)


async def _payee_dialog(
    request: Request,
    service: FinanceService,
    owner_user_id: int | None,
    txns: list[FinanceTransaction],
    show_account: bool,
    errors: list[str] | None = None,
    status_code: int = 200,
) -> Response:
    return templates.TemplateResponse(
        request=request,
        name="partials/transactions/payee.html",
        context={
            "title": await _title(service, txns, owner_user_id),
            "transaction_ids": [t.id for t in txns],
            "show_account": show_account,
            "merchants": await _payee_options(service, owner_user_id),
            "current": _shared_merchant(txns),
            "errors": errors or [],
        },
        status_code=status_code,
    )


@router.post("/payee", include_in_schema=False)
async def payee(
    request: Request,
    transaction_ids: Annotated[list[int], Form()] = [],
    named_ids: Annotated[list[int], Form()] = [],
    merchant_id: Annotated[str, Form()] = "",
    new_name: Annotated[str, Form()] = "",
    category_id: Annotated[str, Form()] = "",
    apply_category: Annotated[bool, Form()] = False,
    show_account: Annotated[bool, Form()] = False,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    """Name the payee, then offer to make it stick.

    A typed name creates the payee first. After naming a SINGLE row its
    payee-less lookalikes are offered, ticked, alongside the category the
    payee mostly uses — one dialog, because they are one decision about
    one payee, and asking twice for one click is worse than asking once.
    After a bulk assign the lookalike sweep is skipped: the rows were
    already chosen by hand, and re-asking second-guesses that.

    ``named_ids`` is what the offer was opened ON, carried back so the
    reply can re-render it. ``transaction_ids`` holds only the lookalikes
    the user ticked, and after a bulk assign there are none - so rendering
    that alone answered with no rows at all, and the category the user had
    just confirmed was written to the ledger and nowhere on the screen.
    """
    # Everything this request acts on: the rows the offer was opened on,
    # plus any lookalikes ticked in it. On the first post the offer does
    # not exist yet and this is just the selection.
    touched = list(dict.fromkeys([*named_ids, *transaction_ids]))
    txns = await _txns(service, touched, owner_user_id)
    label = new_name.strip()
    if not merchant_id and not label:
        return await _payee_dialog(
            request,
            service,
            owner_user_id,
            txns,
            show_account,
            errors=["Pick a payee or name a new one."],
            status_code=422,
        )
    if label:
        created = await create_merchant(
            MerchantCreate(name=label), service=service, owner_user_id=owner_user_id
        )
        chosen, merchant_name = created.id, created.name
    else:
        chosen = int(merchant_id)
        merchant_name = (await service.merchant_names({chosen})).get(
            chosen, "this payee"
        )

    similar = None
    if len(txns) == 1:
        similar = await similar_transactions(
            txns[0].id, service=service, owner_user_id=owner_user_id
        )

    chosen_category = int(category_id) if apply_category and category_id else None
    await assign_merchant(
        MerchantAssign(
            transaction_ids=touched,
            merchant_id=chosen,
            category_id=chosen_category,
        ),
        service=service,
        owner_user_id=owner_user_id,
    )
    if chosen_category is not None:
        # Settle EVERY row this payee covers, which is what the dialog
        # promises. This read a page of 500 and re-filed those, so a
        # payee with more kept the rest - and the offer, which tallies
        # all of them to decide whether to ask, saw the leftovers and
        # asked again. Apply, see the categories change behind the
        # dialog, get the same question back (Shop Rite: 536 rows).
        await service.file_payee_under(
            chosen, chosen_category, owner_user_id=owner_user_id
        )
    await service.db.commit()

    summary = await merchant_category_summary(
        chosen, service=service, owner_user_id=owner_user_id
    )
    lookalikes = similar.items if similar and similar.total else []
    suggested = _followup(summary)
    ask = bool(lookalikes or suggested)
    fresh = await _txns(service, touched, owner_user_id)
    response = await _rows_response(
        request,
        service,
        fresh,
        owner_user_id,
        show_account,
        name="partials/transactions/rows_with_offer.html"
        if ask
        else "partials/transactions/rows.html",
        extra={
            "similar": lookalikes,
            "merchant_id": chosen,
            "merchant_name": merchant_name,
            # NOT "categories": that key is the row selects' option list,
            # from rows_context. Sharing it meant the offer's own list
            # overwrote it - and on the post that settles the payee the
            # offer has no list, so every row came back with an empty
            # select and could not show the category just written to it.
            "offer_categories": (await list_category_options(service=service)).items
            if suggested
            else [],
            "named_ids": touched,
            "offer_category": suggested is not None,
            "suggested_category_id": suggested,
        },
    )
    if ask:
        return response
    return close_dialog(
        with_toast(response, f"Payee set on {len(fresh)} as {merchant_name}")
    )


def _followup(summary: Any) -> int | None:
    """The category to suggest, or None when there is nothing to settle.

    Only ask when the payee argues with itself: more than one category in
    use, or rows with none at all. A payee already filed 21 of 21 the same
    way needs no dialog, and re-confirming what is already true is just a
    click to dismiss.
    """
    if not summary.total:
        return None
    unsettled = (
        summary.distinct_categories > 1 or summary.dominant_count < summary.total
    )
    if not unsettled:
        return None
    return summary.dominant_category_id or summary.default_category_id


@router.post("/delete", include_in_schema=False)
async def delete_selected(
    request: Request,
    transaction_ids: Annotated[list[int], Form()] = [],
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    txns = await _txns(service, transaction_ids, owner_user_id)
    return await _delete(
        request, service, owner_user_id, [t.id for t in txns if t.id is not None]
    )


@router.delete("/{transaction_id}", include_in_schema=False)
async def delete(
    request: Request,
    transaction_id: int,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    await _txns(service, [transaction_id], owner_user_id)
    return await _delete(request, service, owner_user_id, [transaction_id])


async def _delete(
    request: Request, service: FinanceService, owner_user_id: int | None, ids: list[int]
) -> Response:
    """Soft-delete: the rows leave the register (deleted out of band) and
    the uncategorised count follows."""
    await service.soft_delete_transactions(ids, owner_user_id=owner_user_id)
    await service.db.commit()
    response = templates.TemplateResponse(
        request=request,
        name="partials/transactions/deleted.html",
        context={
            "deleted_ids": ids,
            "uncategorized_total": await uncategorized_total(service, owner_user_id),
        },
    )
    plural = "s" if len(ids) != 1 else ""
    return with_toast(response, f"Removed {len(ids)} transaction{plural}")


# --- splits -----------------------------------------------------------------


async def _split_dialog(
    request: Request,
    service: FinanceService,
    txn: FinanceTransaction,
    errors: list[str],
    status_code: int = 200,
    parts: list[dict[str, Any]] | None = None,
) -> Response:
    categories = (await list_category_options(service=service)).items
    blank = {"amount": "", "category_id": None, "memo": ""}
    rows = parts or [blank, blank, blank]
    return templates.TemplateResponse(
        request=request,
        name="partials/transactions/split.html",
        context={"txn": txn, "categories": categories, "parts": rows, "errors": errors},
        status_code=status_code,
    )


@router.get("/{transaction_id}/split", include_in_schema=False)
async def split_form(
    request: Request,
    transaction_id: int,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    (txn,) = await _txns(service, [transaction_id], owner_user_id)
    return await _split_dialog(request, service, txn, [])


@router.post("/{transaction_id}/split", include_in_schema=False)
async def split(
    request: Request,
    transaction_id: int,
    amount: Annotated[list[str], Form()] = [],
    category_id: Annotated[list[str], Form()] = [],
    memo: Annotated[list[str], Form()] = [],
    show_account: Annotated[bool, Form()] = False,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    """State the parts you know; the service fills the remainder. Parts
    are parallel form lists; blank amounts are ignored."""
    (txn,) = await _txns(service, [transaction_id], owner_user_id)
    stated = [
        {"amount": a, "category_id": (c or None), "memo": m}
        for a, c, m in zip(
            amount,
            category_id + [""] * len(amount),
            memo + [""] * len(amount),
            strict=False,
        )
    ]
    parts: list[SplitPart] = []
    errors: list[str] = []
    for row in stated:
        if not row["amount"].strip():
            continue
        cents = money_to_cents(row["amount"])
        if not cents:
            errors.append(f"Not an amount: {row['amount']!r}.")
            continue
        parts.append(
            SplitPart(
                amount=abs(cents),
                category_id=int(row["category_id"]) if row["category_id"] else None,
                memo=row["memo"] or None,
            )
        )
    if not parts and not errors:
        errors.append("State at least one part.")
    if not errors:
        try:
            await split_transaction(
                transaction_id,
                TransactionSplitRequest(parts=parts),
                service=service,
                owner_user_id=owner_user_id,
            )
        except HTTPException as exc:
            errors.append(str(exc.detail))
        except ValueError as exc:
            errors.append(str(exc))
    if errors:
        return await _split_dialog(request, service, txn, errors, 422, parts=stated)
    await service.db.commit()
    fresh = await _txns(service, [transaction_id], owner_user_id)
    response = await _rows_response(
        request, service, fresh, owner_user_id, show_account
    )
    return close_dialog(with_toast(response, f"Split into {len(parts) + 1} lines"))


@router.delete("/{transaction_id}/split", include_in_schema=False)
async def unsplit(
    request: Request,
    transaction_id: int,
    show_account: bool = False,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    await _txns(service, [transaction_id], owner_user_id)
    await unsplit_transaction(
        transaction_id, service=service, owner_user_id=owner_user_id
    )
    await service.db.commit()
    fresh = await _txns(service, [transaction_id], owner_user_id)
    return await _rows_response(request, service, fresh, owner_user_id, show_account)


# --- declare recurring ---------------------------------------------------------


@router.get("/declare", include_in_schema=False)
async def declare_form(
    request: Request,
    transaction_ids: list[int] = Query(default=[]),
    show_account: bool = False,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    """What the selection would become: the plan the commit executes,
    measured from the transactions (cadence, amount, next date), with
    only the names left to the user."""
    await _txns(service, transaction_ids, owner_user_id)
    note = None
    plan = None
    try:
        plan = await preview_declare_recurring(
            DeclareRecurring(transaction_ids=transaction_ids),
            service=service,
            owner_user_id=owner_user_id,
        )
    except HTTPException as exc:
        # The API refuses a selection with no rhythm in it (one payment);
        # that reason is the dialog's whole content.
        note = str(exc.detail)
    return templates.TemplateResponse(
        request=request,
        name="partials/transactions/declare.html",
        context={
            "plan": plan,
            "note": note,
            "transaction_ids": transaction_ids,
            "show_account": show_account,
        },
    )


@router.post("/declare", include_in_schema=False)
async def declare(
    request: Request,
    transaction_ids: Annotated[list[int], Form()] = [],
    show_account: Annotated[bool, Form()] = False,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    """Commit the plan; ``name:<key>`` fields rename the bills it creates."""
    txns = await _txns(service, transaction_ids, owner_user_id)
    form = await request.form()
    names = {
        str(key).removeprefix("name:"): str(value).strip()
        for key, value in form.multi_items()
        if str(key).startswith("name:") and str(value).strip()
    }
    created = await declare_recurring_transactions(
        DeclareRecurring(transaction_ids=transaction_ids, names=names),
        service=service,
        owner_user_id=owner_user_id,
    )
    await service.db.commit()
    fresh = await _txns(
        service, [t.id for t in txns if t.id is not None], owner_user_id
    )
    response = await _rows_response(
        request, service, fresh, owner_user_id, show_account
    )
    count = created.get("streams", created.get("created", len(names) or 1))
    return close_dialog(
        with_toast(
            response, f"{count} recurring bill{'s' if count != 1 else ''} declared"
        )
    )
