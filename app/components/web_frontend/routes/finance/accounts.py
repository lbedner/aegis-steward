"""The accounts section: grouped list on the left, one account's detail on
the right. ``/accounts`` is the combined view; ``/accounts/{id}`` selects
one. Both render the same template, so a list click can take the detail
column (``hx-select``) and refresh the list highlight (``hx-select-oob``)
from a single response.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from starlette.responses import Response

from app.components.backend.api.finance.accounts import list_accounts
from app.components.web_frontend.filters import money_to_cents
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
)
from app.services.finance.constants import (
    ACCOUNT_GROUPS,
    ADD_ACCOUNT_TYPES,
    account_actions,
    account_classification,
    account_group,
)
from app.services.finance.deps import get_finance_service, get_owner_user_id
from app.services.finance.domains.ledger.accounts import effective_balance
from app.services.finance.schemas import AccountResponse
from app.services.finance.service import FinanceService

SECTION = section("accounts")
router = APIRouter()

# The Manage menu: action key (see account_actions) -> label, dialog URL.
ACTION_LABELS = {
    "rename": "Rename",
    "reconcile": "Reconcile",
    "property": "Property details",
    "valuations": "Valuation history",
    "secured_by": "Secured by",
    "remove": "Remove",
}


def balance(account: AccountResponse) -> int:
    return effective_balance(
        current_balance=account.current_balance,
        balance_as_of=account.balance_as_of,
        classification=account.classification,
        activity_balance=account.activity_balance,
    )


def grouped(accounts: list[AccountResponse]) -> list[dict[str, Any]]:
    """Ledger-order groups, each with a subtotal, largest balance first."""
    buckets: dict[str, list[AccountResponse]] = {}
    for account in accounts:
        buckets.setdefault(account_group(account.account_type), []).append(account)
    groups = []
    for label, _types in ACCOUNT_GROUPS:
        members = buckets.get(label)
        if not members:
            continue
        rows = sorted(members, key=balance, reverse=True)
        groups.append(
            {
                "label": label,
                "subtotal": sum(balance(a) for a in rows),
                "accounts": [{"account": a, "balance": balance(a)} for a in rows],
            }
        )
    return groups


def statement_line(account: AccountResponse) -> str | None:
    """``Due Jul 15 · min $35.00`` under a credit account, when reported."""
    liability = account.liability
    if liability is None:
        return None
    parts: list[str] = []
    if liability.next_payment_due_date:
        due = liability.next_payment_due_date
        parts.append(f"Due {due:%b} {due.day}")
    if liability.minimum_payment_amount is not None:
        from app.components.web_frontend.filters import money

        parts.append(f"min {money(liability.minimum_payment_amount, account.currency)}")
    return " · ".join(parts) or None


def actions(account: AccountResponse) -> list[dict[str, str]]:
    keys = account_actions(
        account_type=account.account_type,
        classification=account.classification,
        is_manual=account.is_manual,
    )
    return [
        {
            "key": key,
            "label": ACTION_LABELS[key],
            "url": f"{SECTION.path}/{account.id}/{key}",
        }
        for key in keys
    ]


async def _page(
    request: Request,
    service: FinanceService,
    owner_user_id: int | None,
    account_id: int | None,
    filters: RegisterFilters,
) -> Response:
    listing = await list_accounts(
        include_hidden=False,
        page=1,
        page_size=200,
        service=service,
        owner_user_id=owner_user_id,
    )
    selected = next((a for a in listing.items if a.id == account_id), None)
    if account_id is not None and selected is None:
        raise HTTPException(status_code=404)
    path = SECTION.path if selected is None else f"{SECTION.path}/{selected.id}"
    register = await register_context(
        path=path,
        account=selected,
        accounts=listing.items,
        filters=filters,
        service=service,
        owner_user_id=owner_user_id,
    )
    return render(
        request,
        "pages/accounts.html",
        {
            "section": SECTION,
            "groups": grouped(listing.items),
            "total": sum(balance(a) for a in listing.items),
            "selected": selected,
            "selected_balance": balance(selected) if selected else None,
            "statement_line": statement_line(selected) if selected else None,
            "actions": actions(selected) if selected else [],
            "statement_lines": {a.id: statement_line(a) for a in listing.items},
            "register": register,
        },
    )


@router.get(SECTION.path, include_in_schema=False)
async def page(
    request: Request,
    filters: RegisterFilters = Depends(register_filters),
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    return await _page(request, service, owner_user_id, None, filters)


@router.get(SECTION.path + "/{account_id:int}", include_in_schema=False)
async def account(
    request: Request,
    account_id: int,
    filters: RegisterFilters = Depends(register_filters),
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    return await _page(request, service, owner_user_id, account_id, filters)


def _new_form(
    request: Request, errors: list[str], status_code: int = 200, **values: Any
) -> Response:
    return templates.TemplateResponse(
        request=request,
        name="partials/accounts/new.html",
        context={"types": ADD_ACCOUNT_TYPES, "errors": errors, **values},
        status_code=status_code,
    )


@router.get(SECTION.path + "/new", include_in_schema=False)
async def new_form(request: Request) -> Response:
    return _new_form(request, [], name="", account_type="checking", opening_balance="")


@router.post(SECTION.path + "/new", include_in_schema=False)
async def create(
    request: Request,
    name: Annotated[str, Form()] = "",
    account_type: Annotated[str, Form()] = "checking",
    opening_balance: Annotated[str, Form()] = "",
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    """Create a manual account. Bad input re-renders the form with a 422;
    success closes the dialog and navigates the content area to it."""
    label = name.strip()
    cents = money_to_cents(opening_balance)
    errors = []
    if not label:
        errors.append("Give the account a name.")
    if account_type not in dict(ADD_ACCOUNT_TYPES):
        errors.append("Pick an account type.")
    if cents is None:
        errors.append("The opening balance is not a number.")
    if errors:
        return _new_form(
            request,
            errors,
            status_code=422,
            name=name,
            account_type=account_type,
            opening_balance=opening_balance,
        )
    assert cents is not None
    classification = account_classification(account_type)
    # A debt's balance is the amount owed; store it as the negative figure
    # the ledger's effective-balance rule reads.
    balance = -abs(cents) if classification == "liability" else cents
    account = await service.create_manual_account(
        owner_user_id=owner_user_id,
        name=label,
        account_type=account_type,
        classification=classification,
    )
    assert account.id is not None
    if balance:
        await service.update_account_balance(
            account.id, current_balance=balance, owner_user_id=owner_user_id
        )
    await service.db.commit()
    response = Response(status_code=200)
    navigate(response, f"{SECTION.path}/{account.id}")
    return close_dialog(with_toast(response, f"Added {label}"))
