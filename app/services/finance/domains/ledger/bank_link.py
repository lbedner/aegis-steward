"""What makes an account a bank's, in one place (#309).

A sync, a place (``connections.placing``), a merge (``merging``) and a
disconnect each change it; a column of it written in three and missed in
the fourth is how a later sync stops finding the account and makes a
copy beside it - the failure #309 is about.
"""

from __future__ import annotations

from app.services.finance.models import FinanceAccount


def link(
    account: FinanceAccount,
    *,
    provider: str,
    connection_id: int | None,
    provider_account_id: str | None,
    persistent_account_id: str | None = None,
) -> None:
    """The bank's account from here on, found by these ids. A connection
    of None is a kept one (#307): the ids stay, for a re-link to adopt."""
    account.provider = provider
    account.is_manual = False
    account.connection_id = connection_id
    account.provider_account_id = provider_account_id
    account.persistent_account_id = persistent_account_id


def unlink(account: FinanceAccount, *, forget: bool = False) -> None:
    """Off its connection. A disconnect keeps the ids - linking the bank
    again adopts the account by them (#308); a merged-away copy ``forget``s
    them, so nothing ever finds it again."""
    account.connection_id = None
    if forget:
        account.provider_account_id = None
        account.persistent_account_id = None
