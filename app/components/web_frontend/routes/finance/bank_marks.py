"""An account's bank as a page shows it: its mark and its link.

Split out of ``accounts`` at the budget. Both are drawn from the bank's
contact - its website is the logo's key and the header's link (#412) -
and a page reads those websites once and hands them to both
(``sites``), never once for the marks and again for the link.
"""

from __future__ import annotations

from app.services.finance.domains.ledger.institutions import websites
from app.services.finance.schemas import AccountResponse
from app.services.finance.service import FinanceService


async def institution_logos(
    service: FinanceService,
    wanted: set[int],
    owner_user_id: int | None,
    sites: dict[int, str | None] | None = None,
) -> dict[int, str]:
    """``{institution id: icon url}`` for these banks, where one resolves:
    a stored logo or domain, else a guess from the bank's name. The one
    path every bank's mark comes through - the portfolio's and the
    Place dialog's alike."""
    from app.services.finance.domains.ledger.merchant_icon import institution_icons

    if not wanted:
        return {}
    banks = [
        i
        for i in await service.list_institutions(owner_user_id=owner_user_id)
        if i.id in wanted
    ]
    icons = await institution_icons(service.db, banks, sites)
    return {i: icon.url for i, icon in icons.items()}


async def account_icons(
    service: FinanceService,
    accounts: list[AccountResponse],
    owner_user_id: int | None,
    sites: dict[int, str | None] | None = None,
) -> dict[int, str]:
    """``{account id: icon url}`` for the accounts whose institution
    resolves to one. An account without a bank has no brand to show and
    falls back to its type's glyph (see ``account_glyph``)."""
    logos = await institution_logos(
        service,
        {a.institution_id for a in accounts if a.institution_id},
        owner_user_id,
        sites,
    )
    return {
        a.id: logos[a.institution_id] for a in accounts if a.institution_id in logos
    }


async def held_with(
    service: FinanceService,
    account: AccountResponse | None,
    owner_user_id: int | None,
    sites: dict[int, str | None],
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
    # The homepage is the bank's contact's (#412), read with the page's
    # marks; an account not in the page's list is read on its own.
    if bank.id not in sites:
        sites = await websites(service.db, [bank.id])
    return {"name": bank.name, "url": sites.get(bank.id) or ""}
