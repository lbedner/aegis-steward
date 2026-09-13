"""The accounts section, split by job.

``/accounts`` is the portfolio: every account by group, with balances,
and nothing else. ``/accounts/all`` and ``/accounts/{id}`` are registers,
each the full width of the content area. Both shapes read one grouping
(``grouped``), and the switcher in a register's header says what the
portfolio says, on demand — so neither page owns the account list.
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
    ACCOUNT_ACTION_LABELS,
    ADD_ACCOUNT_TYPES,
    account_actions,
    account_classification,
    account_sections,
)
from app.services.finance.deps import get_finance_service, get_owner_user_id
from app.services.finance.domains.ledger.accounts import effective_balance
from app.services.finance.schemas import AccountResponse
from app.services.finance.service import FinanceService

SECTION = section("accounts")
router = APIRouter()

# The Manage menu's labels live with the rule that orders them, so this
# menu and Flet's cannot start reading differently.
ACTION_LABELS = ACCOUNT_ACTION_LABELS


def balance(account: AccountResponse) -> int:
    return effective_balance(
        current_balance=account.current_balance,
        balance_as_of=account.balance_as_of,
        classification=account.classification,
        activity_balance=account.activity_balance,
    )


def grouped(accounts: list[AccountResponse]) -> list[dict[str, Any]]:
    """Ledger-order groups, each with a subtotal, largest balance first."""
    groups = []
    for label, members in account_sections(accounts):
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


async def _accounts(
    service: FinanceService, owner_user_id: int | None
) -> tuple[list[AccountResponse], dict[str, Any]]:
    """Every live account, and the shape both pages read it through."""
    listing = await list_accounts(
        include_hidden=False,
        page=1,
        page_size=200,
        service=service,
        owner_user_id=owner_user_id,
    )
    return listing.items, {
        "section": SECTION,
        "groups": grouped(listing.items),
        "total": sum(balance(a) for a in listing.items),
        "statement_lines": {a.id: statement_line(a) for a in listing.items},
        # A brand mark per account, borrowed from the bank it is held at.
        # One resolution for the page, never one per row.
        "account_icons": await _account_icons(service, listing.items, owner_user_id),
    }


async def _account_icons(
    service: FinanceService,
    accounts: list[AccountResponse],
    owner_user_id: int | None,
) -> dict[int, str]:
    """``{account id: icon url}`` for the accounts whose institution
    resolves to one. An account without a bank has no brand to show and
    falls back to its type's glyph (see ``account_glyph``)."""
    from app.services.finance.domains.ledger.merchant_icon import institution_icons

    wanted = {a.institution_id for a in accounts if a.institution_id}
    if not wanted:
        return {}
    banks = [
        i
        for i in await service.list_institutions(owner_user_id=owner_user_id)
        if i.id in wanted
    ]
    icons = await institution_icons(service.db, banks)
    return {
        a.id: icons[a.institution_id].url for a in accounts if a.institution_id in icons
    }


async def _register_page(
    request: Request,
    service: FinanceService,
    owner_user_id: int | None,
    account_id: int | None,
    filters: RegisterFilters,
) -> Response:
    """One register: every account, or one of them."""
    accounts, context = await _accounts(service, owner_user_id)
    selected = next((a for a in accounts if a.id == account_id), None)
    if account_id is not None and selected is None:
        raise HTTPException(status_code=404)
    register = await register_context(
        path=f"{SECTION.path}/{selected.id if selected else 'all'}",
        account=selected,
        accounts=accounts,
        filters=filters,
        service=service,
        owner_user_id=owner_user_id,
    )
    return render(
        request,
        "pages/account_register.html",
        {
            **context,
            "selected": selected,
            "selected_balance": balance(selected) if selected else None,
            "statement_line": statement_line(selected) if selected else None,
            "actions": actions(selected) if selected else [],
            "register": register,
        },
    )


@router.get(SECTION.path, include_in_schema=False)
async def page(
    request: Request,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    """The portfolio. No register: that is the other page's job."""
    _, context = await _accounts(service, owner_user_id)
    return render(request, "pages/accounts.html", context)


@router.get(SECTION.path + "/all", include_in_schema=False)
async def all_accounts(
    request: Request,
    filters: RegisterFilters = Depends(register_filters),
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    return await _register_page(request, service, owner_user_id, None, filters)


@router.get(SECTION.path + "/{account_id:int}", include_in_schema=False)
async def account(
    request: Request,
    account_id: int,
    filters: RegisterFilters = Depends(register_filters),
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    return await _register_page(request, service, owner_user_id, account_id, filters)


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
