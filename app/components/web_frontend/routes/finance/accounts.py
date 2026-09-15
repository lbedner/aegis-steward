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
from app.components.web_frontend.filters import money, money_to_cents
from app.components.web_frontend.nav import account_tabs, section
from app.components.web_frontend.rendering import (
    close_dialog,
    navigate,
    render,
    templates,
    with_toast,
)
from app.components.web_frontend.routes.finance import subjects
from app.components.web_frontend.routes.finance.register import (
    RegisterFilters,
    register_context,
    register_filters,
)
from app.components.web_frontend.routes.finance.valuations import (
    valuation_history,
)
from app.components.web_frontend.seen import remember, watermark
from app.services.finance.constants import (
    ACCOUNT_ACTION_LABELS,
    ADD_ACCOUNT_TYPES,
    INVESTMENT_ACCOUNT_TYPES,
    PROPERTY_ACCOUNT_TYPE,
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


def _loan_terms() -> Any:
    """``loan_terms`` from the cover module, resolved at call time - it
    imports this one, so the edge only goes one way when Python loads."""
    from app.components.web_frontend.routes.finance.cover import loan_terms

    return loan_terms


def _filed_count() -> Any:
    """``account_documents``, resolved at call time.

    ``documents`` imports this module for the tab bar and the header, so
    the edge only goes one way when Python loads. The account pages ask
    for their document count when they render, not at import.
    """
    from app.components.web_frontend.routes.finance.documents import account_documents

    return account_documents


async def _accounts(
    service: FinanceService,
    owner_user_id: int | None,
    seeing: str | None = None,
) -> tuple[list[AccountResponse], dict[str, Any]]:
    """Every live account, and the shape both pages read it through.

    ``seeing`` is the ``?whose=`` parameter. It defaults to ours, so the
    portfolio total on this page is a statement about our money however
    many other people's accounts the app is holding.
    """
    listing = await list_accounts(
        include_hidden=False,
        page=1,
        page_size=200,
        subject_id=subjects.whose(seeing),
        service=service,
        owner_user_id=owner_user_id,
    )
    return listing.items, {
        "section": SECTION,
        **await subjects.chips(service, seeing),
        "groups": grouped(listing.items),
        "total": sum(balance(a) for a in listing.items),
        "statement_lines": {a.id: statement_line(a) for a in listing.items},
        # A brand mark per account, borrowed from the bank it is held at.
        # One resolution for the page, never one per row.
        "account_icons": await _account_icons(service, listing.items, owner_user_id),
    }


async def _one_account(
    service: FinanceService, owner_user_id: int | None, account_id: int
) -> tuple[list[AccountResponse], AccountResponse, dict[str, Any]]:
    """One account and the portfolio it sits in, or a 404.

    Whose money it is decides which portfolio that IS: a parent's
    pension sits among their accounts, ours among ours. The listing
    defaults to the household, right for a total and wrong for a door -
    so the account says whose it is and is drawn in that world.
    """
    found = await service.get_account(account_id, owner_user_id=owner_user_id)
    whose = str(found.subject_id) if found and found.subject_id else None
    accounts, context = await _accounts(service, owner_user_id, whose)
    selected = next((a for a in accounts if a.id == account_id), None)
    if selected is None:
        raise HTTPException(status_code=404)
    return accounts, selected, context


async def _last_updated(
    service: FinanceService, account: AccountResponse | None
) -> dict[str, str]:
    """When this account last learned anything, and how stale that is.

    WHERE the answer comes from depends on what fills the account: a
    connected one is as current as its last sync, an investment account
    as current as its newest holdings date, a property as its newest
    valuation. Asking one question of four sources beats four surfaces
    each picking a different date and calling it "updated".
    """
    from app.components.web_frontend.filters import freshness

    if account is None:
        return {}
    if account.connection_id:
        from app.services.finance.adapters.providers.queries import (
            connection_by_id_live,
        )

        connection = await connection_by_id_live(service.db, account.connection_id)
        return freshness(
            connection.last_successful_sync_at if connection else None, "sync"
        )
    if account.account_type in INVESTMENT_ACCOUNT_TYPES:
        held = await service.list_current_holdings(account_id=account.id)
        newest = max((h.as_of_date for h, _security, _value in held), default=None)
        return freshness(newest, "holdings")
    if account.account_type == PROPERTY_ACCOUNT_TYPE:
        history = await valuation_history(service, account, None)
        newest = max((row["as_of_date"] for row in history), default=None)
        return freshness(newest, "valuation")
    from app.services.finance.domains.ledger.queries.transactions import (
        newest_transaction_date,
    )

    return freshness(await newest_transaction_date(service.db, account.id))


async def _header_context(
    service: FinanceService,
    selected: AccountResponse | None,
    owner_user_id: int | None,
) -> dict[str, Any]:
    """What ``account_header`` renders: which account, what it is worth,
    the facts that identify it, and what can be done to it.

    One function to one macro. Built per page, Documents quietly shipped
    without the statement line its siblings carried - and that is the
    cheap version of the drift, since the expensive version is three
    headers that disagree about a balance.

    ``None`` is the register's "All accounts": a header with a switcher
    and a total, and nothing to rename.
    """
    return {
        "selected": selected,
        "selected_balance": balance(selected) if selected else None,
        "statement_line": statement_line(selected) if selected else None,
        "held_with": await held_with(service, selected, owner_user_id),
        "whose_name": await subjects.whose_name(
            service, selected.subject_id if selected else None
        ),
        "actions": actions(selected) if selected else [],
        "updated": await _last_updated(service, selected),
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


async def held_with(
    service: FinanceService,
    account: AccountResponse | None,
    owner_user_id: int | None,
) -> dict[str, str] | None:
    """Who this account is WITH, and how to get to them.

    The institution has been a link on the account all along - the
    Manage menu sets it, the portfolio draws its logo - but the account
    itself never SAID whose it was, so the one place you go to ask "what
    is this costing me" could not tell you who to ask. The homepage is
    the point: a debt you cannot reach is one you cannot pay off early.
    """
    if account is None or not account.institution_id:
        return None
    banks = await service.list_institutions(owner_user_id=owner_user_id)
    bank = next((i for i in banks if i.id == account.institution_id), None)
    if bank is None:
        return None
    url = bank.url or (f"https://{bank.domain}" if bank.domain else None)
    return {"name": bank.name, "url": url or ""}


async def _register_page(
    request: Request,
    service: FinanceService,
    owner_user_id: int | None,
    account_id: int | None,
    filters: RegisterFilters,
) -> Response:
    """One register: every account, or one of them.

    Finding the account is ``_one_account``'s job, not a second copy of
    it here - which is what this was, and it kept the 404 the account
    page had just stopped giving: a register tab that refused to open
    the account its own header was naming.
    """
    selected: AccountResponse | None = None
    if account_id is None:
        accounts, context = await _accounts(service, owner_user_id)
    else:
        accounts, selected, context = await _one_account(
            service, owner_user_id, account_id
        )
    register = await register_context(
        path=f"{SECTION.path}/{selected.id if selected else 'all'}",
        seen=watermark(request, "register"),
        account=selected,
        accounts=accounts,
        filters=filters,
        service=service,
        owner_user_id=owner_user_id,
    )
    response = render(
        request,
        "pages/account_register.html",
        {
            **context,
            **await _header_context(service, selected, owner_user_id),
            # A debt's terms above its rows: the cover sheet owns the
            # shaping, and the register borrows it rather than keeping a
            # second copy of what a loan is called.
            "loan_terms": _loan_terms()(selected) if selected else [],
            **(
                account_tabs(
                    selected.id,
                    "register",
                    len(await _filed_count()(service, selected.id)),
                )
                if selected
                else {}
            ),
            "register": register,
        },
    )
    # Recorded after the rows are marked, so this visit's marks survive
    # the refresh that follows it.
    return remember(request, response, "register")


@router.get(SECTION.path, include_in_schema=False)
async def page(
    request: Request,
    whose: str | None = None,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    """The portfolio. No register: that is the other page's job."""
    _, context = await _accounts(service, owner_user_id, whose)
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


async def _new_form(
    request: Request,
    service: FinanceService,
    errors: list[str],
    status_code: int = 200,
    **values: Any,
) -> Response:
    return templates.TemplateResponse(
        request=request,
        name="partials/accounts/new.html",
        context={
            "types": ADD_ACCOUNT_TYPES,
            "people": await subjects.people(service),
            "errors": errors,
            **values,
        },
        status_code=status_code,
    )


@router.get(SECTION.path + "/new", include_in_schema=False)
async def new_form(
    request: Request, service: FinanceService = Depends(get_finance_service)
) -> Response:
    return await _new_form(
        request,
        service,
        [],
        name="",
        account_type="checking",
        opening_balance="",
        whose="",
    )


@router.post(SECTION.path + "/new", include_in_schema=False)
async def create(
    request: Request,
    name: Annotated[str, Form()] = "",
    account_type: Annotated[str, Form()] = "checking",
    opening_balance: Annotated[str, Form()] = "",
    whose: Annotated[str, Form()] = "",
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
        return await _new_form(
            request,
            service,
            errors,
            status_code=422,
            name=name,
            account_type=account_type,
            opening_balance=opening_balance,
            whose=whose,
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
    # Whose money, said once at creation. The subject row is made here
    # rather than maintained by hand: a person becomes a subject the
    # moment an account is put in their name.
    if whose:
        await subjects.in_someone_elses_name(
            service, account.id, int(whose), owner_user_id
        )
    if balance:
        await service.update_account_balance(
            account.id, current_balance=balance, owner_user_id=owner_user_id
        )
    await service.db.commit()
    response = Response(status_code=200)
    navigate(response, f"{SECTION.path}/{account.id}")
    return close_dialog(with_toast(response, f"Added {label}"))
