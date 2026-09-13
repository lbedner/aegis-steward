"""The accounts section, split by job.

``/accounts`` is the portfolio: every account by group, with balances,
and nothing else. ``/accounts/all`` and ``/accounts/{id}`` are registers,
each the full width of the content area. Both shapes read one grouping
(``grouped``), and the switcher in a register's header says what the
portfolio says, on demand — so neither page owns the account list.
"""

from __future__ import annotations

from functools import partial
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from starlette.responses import Response

from app.components.backend.api.finance.accounts import list_accounts
from app.components.web_frontend import ranges
from app.components.web_frontend.filters import dollars, money, money_to_cents
from app.components.web_frontend.nav import section
from app.components.web_frontend.rendering import (
    close_dialog,
    dialog,
    dialog_done,
    hx_dialog,
    navigate,
    render,
    templates,
    where_from,
    with_toast,
)
from app.components.web_frontend.routes.finance.register import (
    RegisterFilters,
    register_context,
    register_filters,
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
    account_tag,
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


def loan_terms(account: AccountResponse) -> list[dict[str, str]]:
    """The debt's terms as strip cells, or nothing to draw.

    The detail row has held these all along and one line of the header
    read two of them; the rate, what is still owed and where the loan
    started had nowhere to appear at all. A debt whose cost is invisible
    is one nobody can weigh against another.
    """
    from app.services.finance.domains.detection.insights.formatting import format_apr

    liability = account.liability
    if liability is None:
        return []
    cells: list[dict[str, str]] = []
    # Owed FIRST: it is the number the question starts from, and it is
    # the lender's own figure rather than the ledger's running balance.
    if liability.outstanding_balance is not None:
        cells.append(
            {
                "label": "Owed",
                "value": money(liability.outstanding_balance, account.currency),
                "caption": "as the lender reports it",
            }
        )
    # ``apr_bps`` is the one answer for both shapes: a card's purchase
    # APR where it has one, a loan's flat rate where it does not.
    if liability.apr_bps is not None:
        cells.append({"label": "Rate", "value": format_apr(liability.apr_bps)})
    if liability.minimum_payment_amount is not None:
        cells.append(
            {
                "label": "Payment",
                "value": money(liability.minimum_payment_amount, account.currency),
                "caption": (
                    f"due {liability.next_payment_due_date:%b %-d}"
                    if liability.next_payment_due_date
                    else "minimum"
                ),
            }
        )
    if liability.origination_date:
        cells.append(
            {
                "label": "Since",
                "value": f"{liability.origination_date:%b %Y}",
                "caption": (
                    f"{liability.loan_term_months} month term"
                    if liability.loan_term_months
                    else ""
                ),
            }
        )
    # The strip draws 2, 3 or 4 columns; one cell alone is a sentence,
    # not a strip, and belongs on the header line that already exists.
    return cells[:4] if len(cells) > 1 else []


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


async def _one_account(
    service: FinanceService, owner_user_id: int | None, account_id: int
) -> tuple[list[AccountResponse], AccountResponse, dict[str, Any]]:
    """One account and the portfolio it sits in, or a 404.

    Every face of an account needs the whole list anyway - the switcher
    in its header names all of them - so the account is picked out of
    that list rather than fetched again.
    """
    accounts, context = await _accounts(service, owner_user_id)
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
    """One register: every account, or one of them."""
    accounts, context = await _accounts(service, owner_user_id)
    selected = next((a for a in accounts if a.id == account_id), None)
    if account_id is not None and selected is None:
        raise HTTPException(status_code=404)
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
            "loan_terms": loan_terms(selected) if selected else [],
            **(
                account_tabs(
                    selected.id,
                    "register",
                    len(await account_documents(service, selected.id)),
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


def account_tabs(
    account_id: int, current: str, filed: int = 0
) -> dict[str, Any]:
    """An account's two faces, as the sub-nav every section uses.

    The register answers "what happened here", the cover sheet answers
    "what IS this", and Documents is the paper both of them are read
    against. One page could not do all three without the facts
    scrolling away above a thousand rows.

    ``filed`` puts the count on the tab, because the useful thing to
    know about an account's paper before you click is whether there is
    any.
    """
    base = f"{SECTION.path}/{account_id}"
    return {
        "nav_label": "Account",
        "nav_id": "account-tabs",
        "current_tab": current,
        "sub_nav": [
            {"key": "cover", "label": "Overview", "href": f"{base}/overview"},
            {
                "key": "documents",
                "label": "Documents",
                "href": f"{base}/documents",
                "count": filed,
            },
            {"key": "register", "label": "Register", "href": base},
        ],
    }


@router.get(SECTION.path + "/{account_id:int}/overview", include_in_schema=False)
async def cover(
    request: Request,
    account_id: int,
    days: int = ranges.ALL,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    """The account's cover sheet: everything true about it, by kind."""
    accounts, selected, context = await _one_account(
        service, owner_user_id, account_id
    )
    filed = await account_documents(service, account_id)
    valuations = await valuation_history(service, selected, owner_user_id)
    # The window narrows the LINE and the rows under it together: a
    # table that disagrees with the chart above it is worse than either.
    valuations = in_window(valuations, days, selected)
    windows = list(ranges.LONG_WINDOWS)
    if purchase_index(valuations, selected) is not None or ranges.relative_to_purchase(
        days
    ):
        windows += list(ranges.PURCHASE_WINDOWS)
    return render(
        request,
        "pages/account_cover.html",
        {
            **context,
            **account_tabs(account_id, "cover", len(filed)),
            **await _header_context(service, selected, owner_user_id),
            "loan_terms": loan_terms(selected),
            "payoff": payoff_terms(selected),
            "valuations": valuations,
            "valuation_chart": valuation_chart(valuations, selected),
            "secured": await secured_strip(
                service, selected, accounts, owner_user_id
            ),
            "value_ranges": windows,
            "days": days,
            "valuation_columns": list(VALUATION_COLUMNS),
        },
    )


DOCUMENT_API = "/api/v1/documents"
# One path for a filed document; the viewer and the edit form both hang
# off it, so the account id cannot drift out of one of them.
DOCUMENTS = SECTION.path + "/{account_id:int}/documents/{document_id:int}"


async def _filed_document(
    service: FinanceService, account_id: int, document_id: int
) -> Any:
    """The document, if it is filed against THIS account.

    The URL names both, and filing is what makes a document reachable
    from an account - so a number guessed into the path gets a 404
    rather than somebody else's paper.
    """
    from app.services.documents.service import DocumentService

    documents = DocumentService(service.db)
    found = await documents.get(document_id)
    if found is None or account_tag(account_id) not in await documents.tags_for(
        document_id
    ):
        raise HTTPException(status_code=404)
    return found


async def _document_dialog(
    request: Request,
    service: FinanceService,
    account_id: int,
    document_id: int,
    status_code: int = 200,
    errors: list[str] | None = None,
) -> Response:
    """The document beside what we say about it, in the one modal.

    Both halves in one body because they are read together: somebody
    opening a filed document is checking a figure against the page and
    correcting what it was filed as, and making that two dialogs is
    making them click twice to do one thing.
    """
    from app.components.web_frontend.filters import short_date
    from app.services.documents.models import DOCUMENT_KINDS
    from app.services.documents.queries import pages_for

    found = await _filed_document(service, account_id, document_id)
    pages = await pages_for(service.db, document_id)
    return dialog(
        request,
        "partials/accounts/document.html",
        status_code,
        document=found,
        dated=short_date(found.document_date or found.received_at),
        content=f"{DOCUMENT_API}/{document_id}/content",
        kinds=DOCUMENT_KINDS,
        post=f"{SECTION.path}/{account_id}/documents/{document_id}",
        errors=errors or [],
        pages=[
            {
                "number": page.page_number,
                "read": page.status == "read",
                # How it was read, so a figure quoted off this page can
                # say where it came from.
                "method": page.method,
                "text": page.text or "",
                "detail": page.detail or "",
            }
            for page in pages
        ],
    )


@router.get(SECTION.path + "/{account_id:int}/documents", include_in_schema=False)
async def documents_page(
    request: Request,
    account_id: int,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    """The paper filed against this account, on its own page."""
    _, selected, context = await _one_account(service, owner_user_id, account_id)
    filed = await account_documents(service, account_id)
    return render(
        request,
        "pages/account_documents.html",
        {
            **context,
            **account_tabs(account_id, "documents", len(filed)),
            **await _header_context(service, selected, owner_user_id),
            "documents": filed,
            "document_columns": list(DOCUMENT_COLUMNS),
        },
    )


@router.get(DOCUMENTS, include_in_schema=False)
async def document(
    request: Request,
    account_id: int,
    document_id: int,
    service: FinanceService = Depends(get_finance_service),
) -> Response:
    """One filed document: the original, its details, and what was read."""
    return await _document_dialog(request, service, account_id, document_id)


@router.post(DOCUMENTS, include_in_schema=False)
async def document_save(
    request: Request,
    account_id: int,
    document_id: int,
    title: Annotated[str, Form()] = "",
    kind: Annotated[str, Form()] = "other",
    document_date: Annotated[str, Form()] = "",
    note: Annotated[str, Form()] = "",
    service: FinanceService = Depends(get_finance_service),
) -> Response:
    """Save what we SAY about the document. The bytes never change: a
    document is what arrived, and correcting it would make the record a
    lie. A refusal re-renders the whole dialog, page beside form, so the
    reader never loses what they were looking at."""
    from datetime import date as date_type

    from app.services.documents.service import DocumentService

    await _filed_document(service, account_id, document_id)

    async def again(errors: list[str]) -> Response:
        return await _document_dialog(
            request, service, account_id, document_id, 422, errors
        )

    if not title.strip():
        return await again(["Give the document a title."])
    dated: date_type | None = None
    if document_date:
        try:
            dated = date_type.fromisoformat(document_date)
        except ValueError:
            return await again(["That date is not a date."])
    try:
        await DocumentService(service.db).update(
            document_id,
            {
                "title": title,
                "kind": kind,
                "document_date": dated,
                "note": note.strip() or None,
            },
        )
    except ValueError as exc:
        return await again([str(exc)])
    await service.db.commit()
    # Back to the tab it was opened from. Documents, usually - and
    # landing on Overview after editing a document is the app deciding
    # you meant to go somewhere else.
    return dialog_done(
        where_from(request, f"{SECTION.path}/{account_id}/documents"),
        f"Saved {title.strip()}",
    )


# What a value history shows. ``note`` is what HAPPENED - a sale, a
# listing, a price change - because a column of numbers cannot tell a
# sale from an asking price.
VALUATION_COLUMNS = (
    {"key": "as_of_date", "label": "Date", "kind": "date"},
    {"key": "note", "label": "Event"},
    {"key": "source", "label": "Source"},
    {"key": "value", "label": "Value", "kind": "money", "align": "right"},
)


async def valuation_history(
    service: FinanceService, account: AccountResponse, owner_user_id: int | None
) -> list[dict[str, Any]]:
    """An asset's value over time, newest first.

    On the PAGE rather than only behind a Manage dialog: what a house
    fell to and what it recovered to is the reason for keeping the
    history, and a history nobody passes is a history nobody reads.
    """
    from app.services.finance.domains.ledger.valuations import list_valuations

    if account.classification != "asset":
        return []
    found = await list_valuations(
        service.db, account.id, owner_user_id=owner_user_id
    )
    return [
        valuation_row(row)
        for row in sorted(found, key=lambda v: v.as_of_date, reverse=True)
    ]





def valuation_chart(
    history: list[dict[str, Any]], account: AccountResponse | None = None
) -> dict[str, Any] | None:
    """An asset's value over time, with the day the OWNER bought it
    marked.

    Oldest first, because a line reads forwards. One mark, because every
    other number on the line is only interesting relative to what was
    actually paid - and a mark per event turns a few hundred monthly
    estimates into a picket fence.

    Which point is the purchase comes from the PRICE the owner recorded,
    not from a note. A price history describes the property and says
    "Sold" about every owner it ever had; the first one of those was a
    stranger's purchase in 2007, and that is where the dot landed.
    """
    if len(history) < 2:
        return None
    series = sorted(history, key=lambda row: row["as_of_date"])
    return {
        "labels": [f"{row['as_of_date']:%b %Y}" for row in series],
        # Dollars, like every other chart in the app. Cents here drew a
        # $711,200 house at seventy million.
        "series": [
            {"label": "Value", "values": [dollars(row["value"]) for row in series]}
        ],
        "events": purchase_mark(series, account),
    }


async def secured_strip(
    service: FinanceService,
    account: AccountResponse,
    accounts: list[AccountResponse],
    owner_user_id: int | None,
) -> list[dict[str, Any]]:
    """What the property is worth against what is secured on it.

    Four cells, in the order the question is asked: what it is worth,
    what is owed on it, what is left, and how much of the value the debt
    is. Every figure DERIVES from the confirmed lien links at read time -
    nothing here is stored, so nothing here can disagree with the
    accounts it came from.

    Each cell carries its own ``explain``: the words for it, and the
    arithmetic that produced THIS house's number rather than a textbook
    one. ``figure()`` renders that; building it here keeps the figure and
    the explanation of the figure in one place, so a change to the
    derivation cannot leave the explanation describing the old one.

    Nothing at all when no debt is linked: an unlinked property showing
    100% equity is a claim nobody made.
    """
    from app.services.finance.domains.detection.insights.formatting import format_apr
    from app.services.finance.domains.ledger.accounts import liability_details
    from app.services.finance.domains.ledger.properties import secured_position

    value = balance(account)
    if account.account_type != PROPERTY_ACCOUNT_TYPE or not value:
        return []
    details = await liability_details(
        service.db, [a.id for a in accounts if a.classification == "liability"]
    )
    owed = {
        detail.account_id: abs(balance(a) or 0)
        for a in accounts
        for detail in [details.get(a.id)]
        if detail is not None and detail.secured_by_account_id == account.id
    }
    position = secured_position(value, list(owed.values()))
    if position is None:
        return []
    named = [a for a in accounts if a.id in owed]
    against = ", ".join(a.name for a in named)
    secured = sum(owed.values())
    cash = partial(money, currency=account.currency)
    rate = format_apr(position.ltv_bps) if position.ltv_bps is not None else "-"
    cells: list[dict[str, Any]] = [
        {
            "key": "value",
            "label": "Value",
            "value": cash(value),
            "explain": {
                "lead": (
                    "What the house is worth today, as last recorded against "
                    "it. An estimate on the record - not an appraisal, and "
                    "not a price anyone has offered. Every other figure here "
                    "is measured against it, so it is the one worth keeping "
                    "current."
                ),
                "math": [("Latest valuation", cash(value))],
                "note": "Value history, below, is where it comes from.",
            },
        },
        {
            "key": "secured",
            "label": "Secured",
            "value": cash(secured),
            "caption": against,
            "explain": {
                "lead": (
                    "What is owed on the debts that name this house as "
                    "collateral. A balance counts here only once its lien is "
                    "linked - an unlinked debt is still yours, but it is not "
                    "against the house."
                ),
                "math": [(a.name, cash(owed[a.id])) for a in named]
                + [("Secured", cash(secured))],
                "note": "Each debt names the house on its own Secured by.",
            },
        },
        {
            "key": "equity",
            "label": "Equity",
            "value": cash(position.equity),
            "tone": "error" if position.equity < 0 else "ok",
            "explain": {
                "lead": (
                    "What would be left if it sold today at that value and "
                    "every lien was paid off."
                ),
                "math": [
                    ("Value", cash(value)),
                    ("Less secured", cash(-secured)),
                    ("Equity", cash(position.equity)),
                ],
                "note": (
                    "Before the cost of selling: commission, transfer taxes "
                    "and anything owed at closing come out of this."
                ),
            },
        },
        {
            "key": "ltv",
            "label": "LTV",
            "value": rate,
            # Not a judgement of the loan: 80% is where a lender starts
            # asking for insurance, which is the number people know.
            "caption": "80% is where PMI usually starts",
            "explain": {
                "lead": (
                    "Loan to value: how much of the house the debt covers. "
                    "It is what a lender reads instead of the balance, "
                    "because it says what is behind the loan."
                ),
                "math": [
                    ("Secured", cash(secured)),
                    ("Divided by value", cash(value)),
                    ("LTV", rate),
                ],
                "note": (
                    "Above 80% is usually where mortgage insurance is "
                    "required; under it is where refinancing gets easy. It is "
                    "a measure of the lender's risk, not a verdict on the loan."
                ),
            },
        },
    ]
    # One opener per cell, hung on here rather than written out four
    # times: a figure that can explain itself says so the same way.
    for cell in cells:
        cell["attrs"] = hx_dialog(
            f"{SECTION.path}/{account.id}/figures/{cell['key']}",
            extra='role="button" tabindex="0"',
        )
    return cells


@router.get(SECTION.path + "/{account_id:int}/figures/{key}", include_in_schema=False)
async def figure(
    request: Request,
    account_id: int,
    key: str,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    """What one figure on the cover sheet means, in the one modal.

    The strip is rebuilt rather than remembered, so the arithmetic in
    the dialog is the arithmetic on the page behind it - read at the
    same moment, from the same links. A key with no cell is a 404: the
    figures a property has depend on what is linked to it.
    """
    accounts, selected, _ = await _one_account(service, owner_user_id, account_id)
    cells = await secured_strip(service, selected, accounts, owner_user_id)
    cell = next((c for c in cells if c["key"] == key), None)
    if cell is None:
        raise HTTPException(status_code=404)
    return dialog(request, "partials/accounts/figure.html", cell=cell)


def purchase_index(
    series: list[dict[str, Any]], account: AccountResponse | None
) -> int | None:
    """Where in this series the owner bought it, by the price they
    recorded. See ``purchase_mark`` for why the price and not the date."""
    paid = getattr(getattr(account, "property", None), "purchase_price", None)
    if not paid:
        return None
    return next(
        (i for i, row in enumerate(series) if row["value"] == paid), None
    )


def in_window(
    history: list[dict[str, Any]], days: int, account: AccountResponse | None
) -> list[dict[str, Any]]:
    """The history a window asks for.

    Most windows are a number of days back from today. Two are not: "the
    run-up to buying it" and "everything since" are positions in this
    asset's own story, and a house is the one thing people ask about
    that way - what it had been doing before they walked in.
    """
    if ranges.relative_to_purchase(days):
        ordered = sorted(history, key=lambda row: row["as_of_date"])
        at = purchase_index(ordered, account)
        if at is None:
            return history
        # The purchase itself belongs to BOTH halves: it is the end of
        # the run-up and the start of what you have done with it.
        window = ordered[: at + 1] if days == ranges.BEFORE_PURCHASE else ordered[at:]
        return sorted(window, key=lambda row: row["as_of_date"], reverse=True)
    start = ranges.since(days)
    if start is None:
        return history
    return [row for row in history if row["as_of_date"] >= start]


def purchase_mark(
    series: list[dict[str, Any]], account: AccountResponse | None
) -> list[dict[str, Any]]:
    """The chart's one annotation: where the owner bought it.

    Matched on the recorded purchase PRICE rather than the recorded
    DATE, because the price is the figure people keep accurately and the
    date is the one that drifts - on the ledger this was written for,
    the price was exactly right and the date was nine months out. Where
    several points share the price, the earliest is the purchase and the
    rest are a valuation that happens to agree with it.
    """
    paid = getattr(getattr(account, "property", None), "purchase_price", None)
    if not paid:
        return []
    for index, row in enumerate(series):
        if row["value"] == paid:
            return [{"at": index, "label": f"Bought · {money(paid)}"}]
    return []


def valuation_row(row: Any) -> dict[str, Any]:
    """One valuation, as the history table draws it.

    Takes the model row or the response shape: the page reads one and
    the Manage dialog the other, and a table drawn two ways is a table
    that will disagree with itself. ``is_estimate`` is only on the
    model, so it is asked for rather than assumed.
    """
    estimate = getattr(row, "is_estimate", False)
    return {
        "as_of_date": row.as_of_date,
        "note": row.note or ("Estimate" if estimate else "-"),
        "source": row.source,
        "value": row.value,
    }


def payoff_terms(account: AccountResponse) -> list[dict[str, str]]:
    """How this debt can be paid DOWN, said plainly or said unknown.

    Absent is not the same as none: a loan whose early-payoff terms
    nobody has checked must not read as a loan with no penalty, because
    that is the reading a payoff plan would be built on.
    """
    from app.services.finance.domains.writes.terms import EXTRA_PAYMENT, PREPAYMENT

    liability = account.liability
    if liability is None or account.classification != "liability":
        return []
    return [
        {
            "label": "Early payoff",
            "value": PREPAYMENT.get(
                liability.prepayment_penalty or "unknown", "not confirmed"
            ),
            "unknown": not liability.prepayment_penalty
            or liability.prepayment_penalty == "unknown",
        },
        {
            "label": "Extra payments",
            "value": EXTRA_PAYMENT.get(
                liability.extra_payment_treatment or "unknown", "not confirmed"
            ),
            "unknown": not liability.extra_payment_treatment
            or liability.extra_payment_treatment == "unknown",
        },
    ]


async def account_documents(
    service: FinanceService, account_id: int
) -> list[dict[str, Any]]:
    """The paper filed against this account.

    A document belongs to an account by TAG - the document service says
    outright that what a tag means differs per application and the
    framework has no business guessing - so steward's meaning is this
    one label, written in one place so nothing has to re-derive it.
    """
    from app.services.documents.service import DocumentService

    # The REQUEST's session, never a second one. Every transaction takes
    # the write lock now (see ``_async_sqlite_emit_begin``), so a nested
    # session inside a request waits for a lock its own caller is
    # holding and times out as "database is locked" - a read deadlocking
    # against a read, which is the one thing the lock change made
    # possible.
    documents, _ = await DocumentService(service.db).list_documents(
        tag=account_tag(account_id)
    )
    from app.components.web_frontend.filters import short_date
    from app.components.web_frontend.glyphs import file_badge

    # Shaped here, not in the template: Jinja has no comprehension, and
    # a table's rows are data anyway.
    return [
        {
            "title": {
                "label": d.title,
                "url": f"{SECTION.path}/{account_id}/documents/{d.id}",
                "badge": file_badge(d.media_type, d.title),
            },
            "kind": d.kind,
            "at": short_date(d.document_date or d.received_at),
            "pages": d.page_count or "",
        }
        for d in documents
    ]


DOCUMENT_COLUMNS = (
    {"key": "title", "label": "Title", "kind": "open"},
    {"key": "kind", "label": "Kind"},
    {"key": "at", "label": "Dated"},
    {"key": "pages", "label": "Pages"},
)


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
