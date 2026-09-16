"""The Manage menu on an account: rename, reconcile, remove.

Each opens in the dialog (pattern 4) and posts back to itself (pattern
1); success closes the dialog and navigates the content area, so the
header and list re-render from the one page route.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi import HTTPException as _HTTPException
from starlette.responses import Response

from app.components.backend.api.finance.accounts import (
    delete_account,
    ingest_valuations,
    list_accounts,
    list_valuations,
    reconcile_account,
    update_account,
    update_property_details,
    update_secured_debt,
)
from app.components.web_frontend.filters import (
    cents_to_input,
    money_to_cents,
    positions_from_text,
)
from app.components.web_frontend.nav import section
from app.components.web_frontend.rendering import (
    dialog,
    dialog_done,
    where_from,
    with_toast,
)
from app.components.web_frontend.routes.finance.valuations import (
    VALUATION_COLUMNS,
    valuation_row,
)
from app.services.finance.deps import get_finance_service, get_owner_user_id
from app.services.finance.domains.ledger.properties import (
    PROPERTY_KINDS,
    VALUATION_SOURCES,
)
from app.services.finance.models import FinanceAccount
from app.services.finance.schemas import (
    AccountResponse,
    AccountUpdate,
    PropertyDetailsUpdate,
    ReconcileRequest,
    SecuredDebtUpdate,
    ValuationBulkRequest,
)
from app.services.finance.service import FinanceService
from app.services.finance.utils import current_date

SECTION = section("accounts")
router = APIRouter(prefix=SECTION.path)

# Where a pasted value history comes from; the user-facing subset of the
# valuation row's source constraint (models/accounts.py).
PASTE_SOURCES = (("manual", "Manual"), ("zillow", "Zillow"), ("kbb", "KBB"))


async def _account(
    service: FinanceService, account_id: int, owner_user_id: int | None
) -> FinanceAccount:
    account = await service.get_account(account_id, owner_user_id=owner_user_id)
    if account is None:
        raise HTTPException(status_code=404)
    return account


@router.get("/{account_id:int}/rename", include_in_schema=False)
async def rename_form(
    request: Request,
    account_id: int,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    account = await _account(service, account_id, owner_user_id)
    return dialog(
        request,
        "partials/accounts/rename.html",
        account=account,
        name=account.name,
        reference=account.reference or "",
        errors=[],
    )


@router.post("/{account_id:int}/rename", include_in_schema=False)
async def rename(
    request: Request,
    account_id: int,
    name: Annotated[str, Form()] = "",
    reference: Annotated[str, Form()] = "",
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    account = await _account(service, account_id, owner_user_id)
    label = name.strip()
    if not label:
        return dialog(
            request,
            "partials/accounts/rename.html",
            422,
            account=account,
            name=name,
            reference=reference,
            errors=["Give the account a name."],
        )
    await update_account(
        account_id,
        AccountUpdate(name=label, reference=reference.strip()),
        service=service,
        owner_user_id=owner_user_id,
    )
    await service.db.commit()
    return dialog_done(
        where_from(request, f"{SECTION.path}/{account_id}"), f"Renamed to {label}"
    )


@router.get("/{account_id:int}/reconcile", include_in_schema=False)
async def reconcile_form(
    request: Request,
    account_id: int,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    account = await _account(service, account_id, owner_user_id)
    return dialog(
        request,
        "partials/accounts/reconcile.html",
        account=account,
        statement_date=current_date(),
        statement_balance="",
        result=None,
        errors=[],
    )


@router.post("/{account_id:int}/reconcile", include_in_schema=False)
async def reconcile(
    request: Request,
    account_id: int,
    statement_date: Annotated[date | None, Form()] = None,
    statement_balance: Annotated[str, Form()] = "",
    preview: Annotated[str, Form()] = "",
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    """``preview`` re-renders the dialog with the register-vs-statement
    delta; without it the delta lands and the account reopens."""
    account = await _account(service, account_id, owner_user_id)
    cents = money_to_cents(statement_balance)
    errors = []
    if statement_date is None:
        errors.append("Pick the statement date.")
    if cents is None or not statement_balance.strip():
        errors.append("Enter the statement balance as a number.")
    if errors:
        return dialog(
            request,
            "partials/accounts/reconcile.html",
            422,
            account=account,
            statement_date=statement_date or current_date(),
            statement_balance=statement_balance,
            result=None,
            errors=errors,
        )
    assert statement_date is not None and cents is not None
    if account.classification == "liability":
        cents = -abs(cents)
    result = await reconcile_account(
        account_id,
        ReconcileRequest(
            statement_date=statement_date,
            statement_balance=cents,
            preview=bool(preview),
        ),
        service=service,
        owner_user_id=owner_user_id,
    )
    if preview:
        return dialog(
            request,
            "partials/accounts/reconcile.html",
            account=account,
            statement_date=statement_date,
            statement_balance=statement_balance,
            result=result,
            errors=[],
        )
    await service.db.commit()
    return dialog_done(
        where_from(request, f"{SECTION.path}/{account_id}"),
        "Reconciled to the statement",
    )


@router.get("/{account_id:int}/remove", include_in_schema=False)
async def remove_form(
    request: Request,
    account_id: int,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    account = await _account(service, account_id, owner_user_id)
    return dialog(request, "partials/accounts/remove.html", account=account)


@router.delete("/{account_id:int}", include_in_schema=False)
async def remove(
    account_id: int,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    account = await _account(service, account_id, owner_user_id)
    await delete_account(account_id, service=service, owner_user_id=owner_user_id)
    await service.db.commit()
    return dialog_done(SECTION.path, f"Removed {account.name}")


# --- property details ------------------------------------------------------


def _property_values(account: FinanceAccount) -> dict[str, Any]:
    summary = AccountResponse.from_row(account).property
    if summary is None:
        return {"property_kind": "primary", "valuation_source": "user"}
    return {
        "property_kind": summary.kind,
        "valuation_source": summary.valuation_source,
        "purchase_price": cents_to_input(summary.purchase_price),
        "purchase_date": summary.purchase_date,
        "down_payment": cents_to_input(summary.down_payment),
        "valuation_as_of": summary.valuation_as_of,
        "address_label": summary.address_label or "",
    }


def _property_form(
    request: Request,
    account: FinanceAccount,
    errors: list[str],
    status_code: int = 200,
    **values: Any,
) -> Response:
    return dialog(
        request,
        "partials/accounts/property.html",
        status_code,
        account=account,
        kinds=PROPERTY_KINDS,
        sources=VALUATION_SOURCES,
        errors=errors,
        **{**_property_values(account), **values},
    )


@router.get("/{account_id:int}/property", include_in_schema=False)
async def property_form(
    request: Request,
    account_id: int,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    account = await _account(service, account_id, owner_user_id)
    return _property_form(request, account, [])


@router.post("/{account_id:int}/property", include_in_schema=False)
async def property_save(
    request: Request,
    account_id: int,
    property_kind: Annotated[str, Form()] = "primary",
    valuation_source: Annotated[str, Form()] = "user",
    purchase_price: Annotated[str, Form()] = "",
    purchase_date: Annotated[date | None, Form()] = None,
    down_payment: Annotated[str, Form()] = "",
    valuation_as_of: Annotated[date | None, Form()] = None,
    address_label: Annotated[str, Form()] = "",
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    account = await _account(service, account_id, owner_user_id)
    price = money_to_cents(purchase_price)
    down = money_to_cents(down_payment)
    errors = []
    if property_kind not in PROPERTY_KINDS:
        errors.append("Pick a property type.")
    if valuation_source not in VALUATION_SOURCES:
        errors.append("Pick where the value comes from.")
    if price is None:
        errors.append("The purchase price is not a number.")
    if down is None:
        errors.append("The down payment is not a number.")
    values = dict(
        property_kind=property_kind,
        valuation_source=valuation_source,
        purchase_price=purchase_price,
        purchase_date=purchase_date,
        down_payment=down_payment,
        valuation_as_of=valuation_as_of,
        address_label=address_label,
    )
    if errors:
        return _property_form(request, account, errors, 422, **values)
    await update_property_details(
        account_id,
        PropertyDetailsUpdate(
            property_kind=property_kind,
            valuation_source=valuation_source,
            purchase_price=price or None,
            purchase_date=purchase_date,
            down_payment=down or None,
            valuation_as_of=valuation_as_of,
            address_label=address_label.strip() or None,
        ),
        service=service,
        owner_user_id=owner_user_id,
    )
    await service.db.commit()
    return dialog_done(f"{SECTION.path}/{account_id}", "Property details saved")


# --- valuation history -----------------------------------------------------


async def _valuations_dialog(
    request: Request,
    service: FinanceService,
    owner_user_id: int | None,
    account: FinanceAccount,
    errors: list[str],
    status_code: int = 200,
    text: str = "",
    source: str = "user",
) -> Response:
    history = await list_valuations(
        account.id, service=service, owner_user_id=owner_user_id
    )
    return dialog(
        request,
        "partials/accounts/valuations.html",
        status_code,
        account=account,
        history=[
            valuation_row(row)
            for row in sorted(history.items, key=lambda v: v.as_of_date, reverse=True)
        ],
        valuation_columns=list(VALUATION_COLUMNS),
        sources=PASTE_SOURCES,
        errors=errors,
        text=text,
        source=source,
    )


@router.get("/{account_id:int}/valuations", include_in_schema=False)
async def valuations_form(
    request: Request,
    account_id: int,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    account = await _account(service, account_id, owner_user_id)
    return await _valuations_dialog(request, service, owner_user_id, account, [])


@router.post("/{account_id:int}/valuations", include_in_schema=False)
async def valuations_paste(
    request: Request,
    account_id: int,
    text: Annotated[str, Form()] = "",
    source: Annotated[str, Form()] = "manual",
    is_estimate: Annotated[bool, Form()] = False,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    """A pasted series (listing-site history, a CSV pair). The service
    parses it all-or-nothing; a parse failure re-renders with the reason."""
    account = await _account(service, account_id, owner_user_id)
    try:
        result = await ingest_valuations(
            account_id,
            ValuationBulkRequest(text=text, source=source, is_estimate=is_estimate),
            service=service,
            owner_user_id=owner_user_id,
        )
    except _HTTPException as exc:
        return await _valuations_dialog(
            request,
            service,
            owner_user_id,
            account,
            [str(exc.detail)],
            422,
            text=text,
            source=source,
        )
    await service.db.commit()
    response = await _valuations_dialog(
        request, service, owner_user_id, account, [], source=source
    )
    return with_toast(response, f"Added {result.added}, updated {result.updated}")


# --- terms -------------------------------------------------------------------


async def _terms_dialog(
    request: Request,
    service: FinanceService,
    owner_user_id: int | None,
    account: FinanceAccount,
    errors: list[str],
    status_code: int = 200,
    values: dict[str, str] | None = None,
) -> Response:
    """The shape this kind of debt has, filled with what is on record."""
    from app.services.finance.domains.writes.terms import shape_for, shown

    terms = shape_for(account.account_type)
    current = await _liability_detail(service, account.id)
    on_record = {
        term.name: _as_input(term, getattr(current, term.name, None)) for term in terms
    }
    return dialog(
        request,
        "partials/accounts/terms.html",
        status_code,
        account=account,
        terms=terms,
        values={**on_record, **(values or {})},
        errors=errors,
        shown=shown,
    )


def _as_input(term: Any, value: Any) -> str:
    """A stored value back in the units the input is typed in."""
    if value is None:
        return ""
    if term.kind == "money":
        return cents_to_input(value)
    if term.kind == "rate":
        return f"{value / 100:g}"
    if term.kind == "date":
        return value.isoformat()
    return str(value)


async def _liability_detail(service: FinanceService, account_id: int) -> Any:
    from app.services.finance.domains.ledger.accounts import liability_details

    details = await liability_details(service.db, [account_id])
    return details.get(account_id)


@router.get("/{account_id:int}/terms", include_in_schema=False)
async def terms_form(
    request: Request,
    account_id: int,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    account = await _account(service, account_id, owner_user_id)
    return await _terms_dialog(request, service, owner_user_id, account, [])


@router.post("/{account_id:int}/terms", include_in_schema=False)
async def terms_save(
    request: Request,
    account_id: int,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    """Save the terms this shape asks for.

    The form is read back through the same declaration that rendered it,
    so a field cannot be written that the shape does not name - and the
    parse rule for a rate lives beside the rule that displays one.
    """
    from app.services.finance.domains.writes.terms import (
        LoanTermsPayload,
        loan_terms_execute,
        shape_for,
        typed,
    )

    account = await _account(service, account_id, owner_user_id)
    form = await request.form()
    terms = shape_for(account.account_type)
    values = {term.name: str(form.get(term.name) or "") for term in terms}
    stated: dict[str, Any] = {}
    errors: list[str] = []
    for term in terms:
        value, why = typed(term, values[term.name])
        if why:
            errors.append(f"{term.label}: {values[term.name]!r} {why}")
        elif value is not None:
            stated[term.name] = value
    if errors:
        return await _terms_dialog(
            request, service, owner_user_id, account, errors, 422, values=values
        )
    # The same executor the approval card runs. A form that wrote the
    # columns itself would be a second implementation of "what recording
    # terms means", and the two would drift the first time one grew a
    # rule - which is exactly what the shape declaration exists to stop.
    await loan_terms_execute(
        service.db,
        LoanTermsPayload(account_id=account.id, **stated),
        owner_user_id,
    )
    await service.db.commit()
    response = await _terms_dialog(request, service, owner_user_id, account, [])
    return with_toast(response, "Terms saved")


# --- positions ---------------------------------------------------------------


async def _positions_dialog(
    request: Request,
    service: FinanceService,
    owner_user_id: int | None,
    account: FinanceAccount,
    errors: list[str],
    status_code: int = 200,
    positions: str = "",
    as_of_date: str = "",
) -> Response:
    from app.components.backend.api.finance.investments import list_account_holdings

    held = await list_account_holdings(
        account.id, service=service, owner_user_id=owner_user_id
    )
    return dialog(
        request,
        "partials/accounts/positions.html",
        status_code,
        account=account,
        errors=errors,
        positions=positions,
        as_of_date=as_of_date or current_date().isoformat(),
        holdings=[
            {
                "ticker": h.ticker,
                "quantity": f"{h.quantity:g}",
                "value": h.market_value,
            }
            for h in held.items
        ],
    )


@router.get("/{account_id:int}/positions", include_in_schema=False)
async def positions_form(
    request: Request,
    account_id: int,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    account = await _account(service, account_id, owner_user_id)
    return await _positions_dialog(request, service, owner_user_id, account, [])


@router.post("/{account_id:int}/positions", include_in_schema=False)
async def positions_save(
    request: Request,
    account_id: int,
    positions: Annotated[str, Form()] = "",
    as_of_date: Annotated[str, Form()] = "",
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    """Pasted positions, all-or-nothing.

    A paste of twenty rows where two are malformed files NOTHING and says
    which two: filing eighteen leaves the account wrong in a way that
    looks right, and the reader has no way to tell which two are missing.
    """
    from app.components.backend.api.finance.investments import upsert_holding
    from app.services.finance.schemas.investments import HoldingCreate

    account = await _account(service, account_id, owner_user_id)
    rows, errors = positions_from_text(positions)
    if not errors and not rows:
        errors = ["Nothing to save - one position per line."]
    try:
        as_of = date.fromisoformat(as_of_date) if as_of_date else current_date()
    except ValueError:
        errors.append(f"{as_of_date!r} is not a date")
        as_of = current_date()
    if errors:
        return await _positions_dialog(
            request,
            service,
            owner_user_id,
            account,
            errors,
            422,
            positions=positions,
            as_of_date=as_of_date,
        )
    for row in rows:
        await upsert_holding(
            account.id,
            HoldingCreate(
                ticker=row["ticker"],
                quantity=row["quantity"],
                price=row["price"],
                as_of_date=as_of,
            ),
            service=service,
            owner_user_id=owner_user_id,
        )
    await service.db.commit()
    response = await _positions_dialog(request, service, owner_user_id, account, [])
    return with_toast(
        response, f"Saved {len(rows)} position{'s' if len(rows) != 1 else ''}"
    )


# --- secured by --------------------------------------------------------------


async def _lien_dialog(
    request: Request,
    service: FinanceService,
    owner_user_id: int | None,
    account: FinanceAccount,
    errors: list[str],
    status_code: int = 200,
    **values: Any,
) -> Response:
    listing = await list_accounts(
        include_hidden=False,
        page=1,
        page_size=200,
        service=service,
        owner_user_id=owner_user_id,
    )
    properties = [a for a in listing.items if a.account_type == "property"]
    # The list item carries the liability detail row; from_row alone does not.
    item = next((a for a in listing.items if a.id == account.id), None)
    liability = item.liability if item else None
    current = {
        "secured_by_account_id": liability.secured_by_account_id if liability else None,
        "lien_position": liability.lien_position if liability else None,
    }
    return dialog(
        request,
        "partials/accounts/secured_by.html",
        status_code,
        account=account,
        properties=properties,
        errors=errors,
        **{**current, **values},
    )


@router.get("/{account_id:int}/secured_by", include_in_schema=False)
async def secured_by_form(
    request: Request,
    account_id: int,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    account = await _account(service, account_id, owner_user_id)
    return await _lien_dialog(request, service, owner_user_id, account, [])


@router.post("/{account_id:int}/secured_by", include_in_schema=False)
async def secured_by_save(
    request: Request,
    account_id: int,
    secured_by_account_id: Annotated[str, Form()] = "",
    lien_position: Annotated[str, Form()] = "",
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    account = await _account(service, account_id, owner_user_id)
    errors = []
    position: int | None = None
    if lien_position.strip():
        try:
            position = int(lien_position)
        except ValueError:
            errors.append("The lien position is a number (1 for a first mortgage).")
    if errors:
        return await _lien_dialog(
            request,
            service,
            owner_user_id,
            account,
            errors,
            422,
            secured_by_account_id=int(secured_by_account_id)
            if secured_by_account_id
            else None,
            lien_position=lien_position,
        )
    await update_secured_debt(
        account_id,
        SecuredDebtUpdate(
            secured_by_account_id=int(secured_by_account_id)
            if secured_by_account_id
            else None,
            lien_position=position,
        ),
        service=service,
        owner_user_id=owner_user_id,
    )
    await service.db.commit()
    return dialog_done(f"{SECTION.path}/{account_id}", "Lien link saved")
