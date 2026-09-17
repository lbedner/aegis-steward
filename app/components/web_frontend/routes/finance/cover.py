"""The cover sheet: what an account IS, rather than what happened in it.

Split out of ``accounts.py`` at the 500-line budget. Sections appear by
KIND - a loan has terms, a property has a value line, a debt secured on
one has an equity position - so this module is where "what can this
account answer?" is decided, and the account pages next door only have
to know that it can be asked.
"""

from __future__ import annotations

from functools import partial
from typing import Any

from fastapi import APIRouter, Depends, Request
from starlette.responses import Response

from app.components.web_frontend import ranges
from app.components.web_frontend.filters import mark_new, money
from app.components.web_frontend.nav import account_tabs, section
from app.components.web_frontend.rendering import dialog, hx_dialog, or_404, render
from app.components.web_frontend.routes.finance.accounts import (
    _filed_count,
    _header_context,
    _one_account,
    balance,
)
from app.components.web_frontend.routes.finance.subjects import who_and_where
from app.components.web_frontend.routes.finance.valuations import (
    VALUATION_COLUMNS,
    in_window,
    purchase_index,
    valuation_chart,
    valuation_history,
)
from app.components.web_frontend.seen import remember, watermark
from app.services.finance.constants import PROPERTY_ACCOUNT_TYPE
from app.services.finance.deps import get_finance_service, get_owner_user_id
from app.services.finance.domains.planning.recurring.upcoming import (
    Total,
    scheduled_by_account,
    totalled,
)
from app.services.finance.schemas import AccountResponse
from app.services.finance.service import FinanceService

SECTION = section("accounts")
router = APIRouter()


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


def cash_terms(account: AccountResponse, scheduled: Total) -> list[dict[str, str]]:
    """A cash account's cover: what is coming, and what it leaves.

    A card's Overview showed what it costs and a property's what it is
    worth; a checking account's showed nothing, which read as broken
    when the figures were there the whole time. What cash is FOR is
    flow, so the strip answers "can I cover the month" rather than
    restating the balance the header already carries.
    """
    from app.services.finance.constants import CASH_ACCOUNT_TYPES
    from app.services.finance.domains.ledger.accounts import effective_balance

    if account.classification != "asset" or account.account_type not in (
        CASH_ACCOUNT_TYPES
    ):
        return []
    if not scheduled.items:
        return []
    balance = effective_balance(
        current_balance=account.current_balance,
        balance_as_of=account.balance_as_of,
        classification=account.classification,
        activity_balance=account.activity_balance,
    )
    counted = f"{scheduled.items} bill{'s' if scheduled.items > 1 else ''} and deposit"
    cells = [
        {
            "label": "Scheduled",
            "value": money(scheduled.net, account.currency),
            "caption": f"{counted}{'s' if scheduled.items > 1 else ''} to come",
        },
        {
            "label": "After",
            "value": money(balance + scheduled.net, account.currency),
            "caption": "if every one lands",
        },
    ]
    # What the bank says can be spent, when it said and it differs: a
    # deposit on hold is on the books and not yet spendable.
    if account.available_balance is not None and account.available_balance != balance:
        cells.insert(
            0,
            {
                "label": "Available",
                "value": money(account.available_balance, account.currency),
                "caption": f"of {money(balance, account.currency)} on the books",
            },
        )
    return cells


@router.get(SECTION.path + "/{account_id:int}/overview", include_in_schema=False)
async def cover(
    request: Request,
    account_id: int,
    days: int = ranges.ALL,
    service: FinanceService = Depends(get_finance_service),
    owner_user_id: int | None = Depends(get_owner_user_id),
) -> Response:
    """The account's cover sheet: everything true about it, by kind."""
    accounts, selected, context = await _one_account(service, owner_user_id, account_id)
    filed = await _filed_count()(service, account_id)
    valuations = await valuation_history(service, selected, owner_user_id)
    # The window narrows the LINE and the rows under it together: a
    # table that disagrees with the chart above it is worse than either.
    valuations = await mark_new(
        service.db,
        in_window(valuations, days, selected),
        watermark(request, "valuations"),
    )
    windows = list(ranges.LONG_WINDOWS)
    if purchase_index(valuations, selected) is not None or ranges.relative_to_purchase(
        days
    ):
        windows += list(ranges.PURCHASE_WINDOWS)
    response = render(
        request,
        "pages/account_cover.html",
        {
            **context,
            **account_tabs(account_id, "cover", len(filed)),
            **await _header_context(service, selected, owner_user_id),
            "loan_terms": loan_terms(selected),
            "cash_terms": cash_terms(
                selected,
                totalled((await scheduled_by_account(service.db)).get(account_id, [])),
            ),
            "payoff": payoff_terms(selected),
            "valuations": valuations,
            "valuation_chart": valuation_chart(valuations, selected),
            "secured": await secured_strip(service, selected, accounts, owner_user_id),
            "value_ranges": windows,
            "days": days,
            "valuation_columns": list(VALUATION_COLUMNS),
            **await who_and_where(service, selected, owner_user_id),
            **await about_the_subject(service, selected),
        },
    )
    return remember(request, response, "valuations")


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
    or_404(cell)
    return dialog(request, "partials/accounts/figure.html", cell=cell)


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


async def about_the_subject(service: FinanceService, account: Any) -> dict[str, Any]:
    """What the app can say about whose money this is, and the cases it
    is caught up in.

    A pension account holds no transactions and never will - the money
    lands in a bank account somewhere else - so what it is FOR is the
    figure somebody has to put on a form and the case that asked for it.
    Both live on the party, and neither was reachable from here.
    """
    from app.services.matters.facts import FactService, drawn, place_book
    from app.services.matters.matters import MatterService

    if account is None or not account.subject_id:
        return {"about": [], "matters": []}
    party_id = next(
        (
            subject.party_id
            for subject in await service.list_subjects()
            if subject.id == account.subject_id and subject.party_id
        ),
        None,
    )
    if party_id is None:
        return {"about": [], "matters": []}
    places = await place_book(service.db)
    # About THIS account, not everything known about its owner: his
    # incidental balance at a nursing home is not a fact about his
    # pension, and a page that lists both is a page you stop reading.
    facts = await FactService(service.db).find(account_id=account.id)
    matters = MatterService(service.db)
    cases = [
        {"id": matter.id, "title": matter.title, "status": matter.status}
        for matter in await matters.find(status="open")
        for link, party in await matters.participants(matter.id)
        if party.id == party_id and link.role == "subject"
    ]
    return {
        "about": [drawn(fact, places) for fact in facts],
        "matters": cases,
        # Where a new one goes: the case if there is one, so a figure
        # recorded here lands where the county is asking for it.
        "fact_matter_id": cases[0]["id"] if cases else None,
        "fact_party_id": party_id,
    }
