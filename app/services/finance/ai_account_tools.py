"""The balance sheet tool: every account, with everything hanging off it.

Split from ``ai_tools`` at the budget, and the seam is the shape of the
answer: the other read tools return rows, this one assembles an account
with its holdings, its envelope, its goal, its liability terms, the
equity a property carries and the bills about to hit it. One wide
payload the sandbox slices, which is why it is long enough to want its
own file.

Whose money is the argument that matters: ours by default, because a
net worth is a statement about the household and a parent's pension
tracked here is not part of it.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from app.core.db import get_async_session
from app.services.ai.domains.chat.tools import register_tool
from app.services.finance.domains.investments.securities import list_current_holdings
from app.services.finance.domains.ledger.accounts import (
    account_transaction_totals,
    effective_balance,
    liability_details,
)
from app.services.finance.domains.ledger.properties import (
    PROPERTY_ACCOUNT_TYPE,
    property_metadata,
    secured_position,
)
from app.services.finance.domains.ledger.queries.accounts import EVERYONE, accounts_page
from app.services.finance.domains.ledger.subjects import subject_filter
from app.services.finance.domains.ledger.valuations import preferred_valuation_row
from app.services.finance.domains.planning.envelopes import envelope_metadata
from app.services.finance.domains.planning.goals import goal_metadata
from app.services.finance.domains.planning.recurring import queries as recurring_queries
from app.services.finance.utils import current_date

# How far ahead accounts() reports scheduled flows: one full monthly
# cycle with margin, so a mid-month plan sees next month's rent.
_UPCOMING_WINDOW_DAYS = 35


async def _named(session: Any, rows: list[Any]) -> tuple[dict[int, str], dict[int, str]]:
    """Subject and institution names for a page of accounts, in two
    queries rather than two per row."""
    from app.services.finance.domains.ledger.institutions import list_institutions
    from app.services.finance.domains.ledger.subjects import list_subjects

    wanted = {row.subject_id for row in rows if row.subject_id}
    banks = {row.institution_id for row in rows if row.institution_id}
    return (
        {s.id: s.name for s in await list_subjects(session) if s.id in wanted},
        {i.id: i.name for i in await list_institutions(session) if i.id in banks},
    )


async def accounts(whose: str = "ours") -> dict[str, Any]:
    """The full balance sheet: every account with its balance and role.

    Args:
        whose: "ours" (default - our own money, which is what a net
            worth or a plan is about), "all", or a subject id. An
            account tracked for a parent in care is not ours: it is
            absent by default, so say whose it is when reporting one.

    Returns a dict with key 'accounts': a list where every entry
    carries 'id', 'name', 'account_type', 'classification',
    'balance_cents' (liabilities are negative = owed), 'hidden',
    'whose' (null = ours, else whose money it is), 'subject_id',
    'held_with' (the institution) and 'reference' (the number that
    institution prints - a member id, a policy number). A balance of 0
    on an account with a 'whose' usually means no figure has been
    recorded, not that it is worth nothing.
    Investment accounts add 'holdings', a list of entries with 'ticker',
    'name', 'quantity' and 'market_value_cents'. Envelope accounts add
    'envelope' with 'credit_cents', 'cadence' and 'auto_credit'. Goal
    accounts add 'goal' with 'target_cents', 'status',
    'monthly_contribution_cents' and 'priority'. Liability accounts add
    'liability' with 'type', 'interest_rate_bps',
    'minimum_payment_cents', 'next_payment_due',
    'last_statement_balance_cents', 'last_payment_cents',
    'last_payment_date', 'ytd_interest_paid_cents' and
    'ytd_principal_paid_cents' when card terms are on file. A property
    that secures linked liabilities adds 'equity_cents', 'ltv_bps'
    (basis points) and 'liens' (account name + lien_position) - derived
    from the confirmed links, absent when nothing is linked. Accounts
    with scheduled activity inside the next 35 days add 'upcoming': a
    date-ordered list of entries with 'name', 'date' and 'amount_cents'
    (signed - inflows positive, bills negative), from the same recurring
    streams the briefing's EXPECTED section reads. Use it to check that
    a plan survives every touched account's near-term debits.
    """
    async with get_async_session() as session:
        account_rows, _count = await accounts_page(
            session,
            owner_user_id=None,
            include_hidden=True,
            page=1,
            page_size=500,
            subject_id=subject_filter(whose),
        )
        holdings = await list_current_holdings(session)
        account_ids = [account.id for account in account_rows]
        activity_totals = await account_transaction_totals(
            session, account_ids=account_ids
        )
        liabilities = await liability_details(session, account_ids)
        # The row that actually drives each property's balance: its source
        # and date are the provenance worth quoting, and they outrank the
        # typed metadata whenever a dated series exists.
        valuation_rows = {
            account.id: await preferred_valuation_row(session, account)
            for account in account_rows
            if account.account_type == PROPERTY_ACCOUNT_TYPE
        }
        streams = await recurring_queries.active_streams(
            session, subject_id=EVERYONE
        )

    today = current_date()
    horizon = today + timedelta(days=_UPCOMING_WINDOW_DAYS)
    upcoming_by_account: dict[int, list[dict[str, Any]]] = {}
    for stream in streams:
        when = stream.next_expected_date
        if stream.account_id is None or when is None:
            continue
        if not today <= when <= horizon:
            continue
        amount = stream.amount
        sign = 1 if stream.direction == "inflow" else -1
        upcoming_by_account.setdefault(stream.account_id, []).append(
            {
                "name": stream.name,
                "date": when.isoformat(),
                "amount_cents": sign * abs(amount),
            }
        )

    balances = {
        account.id: effective_balance(
            current_balance=account.current_balance,
            balance_as_of=account.balance_as_of,
            classification=account.classification,
            activity_balance=activity_totals.get(account.id, 0),
        )
        for account in account_rows
    }
    account_names = {account.id: account.name for account in account_rows}
    # FW-04: liens by the property they encumber. The link is what the
    # user CONFIRMED on the liability (never inferred); equity and LTV
    # derive from it at read time and are never stored.
    liens_by_property: dict[int, list[tuple[int, str, int, int | None]]] = {}
    for detail in liabilities.values():
        if detail.secured_by_account_id is None:
            continue
        liens_by_property.setdefault(detail.secured_by_account_id, []).append(
            (
                detail.lien_position or 99,
                account_names.get(detail.account_id, f"account {detail.account_id}"),
                abs(balances.get(detail.account_id, 0)),
                detail.lien_position,
            )
        )

    holdings_by_account: dict[int, list[dict[str, Any]]] = {}
    for holding, security, market_value_cents in holdings:
        holdings_by_account.setdefault(holding.account_id, []).append(
            {
                "ticker": security.ticker if security else None,
                "name": security.name if security else None,
                "quantity": holding.quantity_e8 / 1e8,
                "market_value_cents": market_value_cents,
            }
        )

    # What the page says beside the balance: whose money it is, who it
    # is held with, and the number the institution prints. Asked what it
    # knows about a pension, the honest answer was "nothing linked" while
    # the account itself named a subject, an institution and a member id.
    whose, held_with = await _named(session, account_rows)

    out: list[dict[str, Any]] = []
    for account in account_rows:
        entry: dict[str, Any] = {
            "id": account.id,
            "name": account.name,
            "account_type": account.account_type,
            "classification": account.classification,
            # Same rule the accounts tab renders: authoritative balance
            # writes win; a never-written 0 falls back to the register sum.
            "balance_cents": balances[account.id],
            "hidden": account.is_hidden,
            # Null is ours, and a balance of 0 with a subject means "no
            # figure recorded yet", never "worth nothing".
            "whose": whose.get(account.subject_id or 0),
            "subject_id": account.subject_id,
            "held_with": held_with.get(account.institution_id or 0),
            "reference": account.reference,
        }
        envelope_meta = envelope_metadata(account.metadata_)
        if envelope_meta is not None:
            # The domain field is named monthly_credit, but the amount is
            # booked once per cadence period - a "monthly" key next to
            # cadence="weekly" reads as a contradiction, and models have
            # mis-stated the allowance because of it.
            entry["envelope"] = {
                "credit_cents": envelope_meta.monthly_credit,
                "cadence": envelope_meta.cadence,
                "auto_credit": envelope_meta.auto_credit,
            }
        property_meta = property_metadata(account.metadata_)
        if property_meta is not None:
            # Provenance rides with the number: an owner's estimate and an
            # appraisal are not interchangeable in a lending conversation,
            # and a bare figure invites the model to present one as the other.
            valuation = valuation_rows.get(account.id)
            entry["property"] = {
                "kind": property_meta.property_kind,
                "valuation_source": (
                    valuation.source
                    if valuation is not None
                    else property_meta.valuation_source
                ),
                "valuation_as_of": (
                    valuation.as_of_date.isoformat()
                    if valuation is not None
                    else property_meta.valuation_as_of.isoformat()
                    if property_meta.valuation_as_of
                    else None
                ),
                "is_estimate": (
                    valuation.is_estimate if valuation is not None else None
                ),
                "purchase_price_cents": property_meta.purchase_price,
                "purchase_date": (
                    property_meta.purchase_date.isoformat()
                    if property_meta.purchase_date
                    else None
                ),
                "ownership_share_bps": property_meta.ownership_share_bps,
                "include_in_net_worth": property_meta.include_in_net_worth,
            }
            liens = sorted(liens_by_property.get(account.id, []))
            position = secured_position(
                balances[account.id], [owed for _, _, owed, _ in liens]
            )
            if position is not None:
                # Absent entirely when nothing is linked: an unlinked
                # property must not read as a 100%-equity claim.
                entry["property"]["equity_cents"] = position.equity
                entry["property"]["ltv_bps"] = position.ltv_bps
                entry["property"]["liens"] = [
                    {"account": name, "lien_position": lien_position}
                    for _, name, _, lien_position in liens
                ]
        goal_meta = goal_metadata(account.metadata_)
        if goal_meta is not None:
            entry["goal"] = {
                "target_cents": goal_meta.target_amount,
                "status": goal_meta.status,
                "monthly_contribution_cents": goal_meta.monthly_contribution,
                "priority": goal_meta.priority,
            }
        if account.id in holdings_by_account:
            entry["holdings"] = holdings_by_account[account.id]
        if account.id in upcoming_by_account:
            entry["upcoming"] = upcoming_by_account[account.id]
        detail = liabilities.get(account.id)
        if detail is not None:
            entry["liability"] = {
                "type": detail.liability_type,
                "interest_rate_bps": detail.interest_rate_bps,
                "minimum_payment_cents": detail.minimum_payment_amount,
                "next_payment_due": (
                    detail.next_payment_due_date.isoformat()
                    if detail.next_payment_due_date
                    else None
                ),
                "last_statement_balance_cents": detail.last_statement_balance,
                "last_payment_cents": detail.last_payment_amount,
                "last_payment_date": (
                    detail.last_payment_date.isoformat()
                    if detail.last_payment_date
                    else None
                ),
                "ytd_interest_paid_cents": detail.ytd_interest_paid,
                "ytd_principal_paid_cents": detail.ytd_principal_paid,
            }
        out.append(entry)
    return {"accounts": out}




register_tool(
    "accounts",
    accounts,
    description="All accounts with balances, holdings, envelopes and goals",
    replace=True,
)
