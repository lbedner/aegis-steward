"""What a UI may do to an account, and what it calls each thing: the
Manage menu's one rule, which the web and Flet frontends both read."""

from __future__ import annotations

from typing import NamedTuple

from app.services.finance.constants import (
    INVESTMENT_ACCOUNT_TYPES,
    PROPERTY_ACCOUNT_TYPE,
    STATED_VALUE_ACCOUNT_TYPES,
)


# Action key -> the label both frontends show. The keys are the rule
# (``account_actions``); this is the one place they are put into words,
# because a menu that reads differently in two places is two menus.
class AccountThing(NamedTuple):
    """One thing about an account: what a page CALLS it, and what the
    menu says you can do to it.

    Both, in one row, because they were drifting apart. The cover sheet
    said "Value history" while the menu offered "Valuation history"; the
    register said "Holdings" while the menu offered "Positions". Two
    names for one thing is a reader wondering whether they are the same
    thing, and they never find out except by clicking.

    ``noun`` is None where nothing on a page is titled after it.
    """

    action: str
    noun: str | None = None


# The menu is read by someone looking for a thing to change, so it says
# what they would go looking for. "Terms" is correct and nobody would
# hunt for it to edit an APR; "Reconcile" is the right accounting word
# and stays the DIALOG's title, where there is room to mean it.
ACCOUNT_THINGS: dict[str, AccountThing] = {
    # Not "Rename": the dialog behind it also holds the number the
    # institution prints, and a menu that says one of the two things it
    # does is a menu somebody closes before finding the other.
    "rename": AccountThing("Name and number"),
    "institution": AccountThing("Set the bank", "Institution"),
    "reconcile": AccountThing("Correct the balance", "Balance"),
    "property": AccountThing("Edit property details", "Property details"),
    "valuations": AccountThing("Edit value history", "Value history"),
    "positions": AccountThing("Edit holdings", "Holdings"),
    "terms": AccountThing("Edit rates & payments", "Rates & payments"),
    "secured_by": AccountThing("Link to collateral", "Collateral"),
    "merge": AccountThing("Merge a duplicate"),
    # Closed is the bank's word: it ended, its history stays (#315).
    # Hidden is ours: out of every list and total, history and all.
    "close": AccountThing("Close"),
    "reopen": AccountThing("Reopen"),
    "hide": AccountThing("Hide"),
    "show": AccountThing("Show"),
    "remove": AccountThing("Remove"),
}

# Accounts that are a share of another's money - a goal's pot, an
# envelope - hidden so it is not counted twice. Never offered to show.
POT_ACCOUNT_TYPES = frozenset({"goal", "envelope"})

# The menu labels, derived. Both frontends import this name.
ACCOUNT_ACTION_LABELS: dict[str, str] = {
    key: thing.action for key, thing in ACCOUNT_THINGS.items()
}


def account_actions(
    *,
    account_type: str,
    classification: str,
    connected: bool,
    hidden: bool | None = None,
    closed: bool | None = None,
) -> tuple[str, ...]:
    """What a UI may offer to do to an account, in menu order.

    Rename, institution and reconcile always; what it is WORTH wherever
    the worth is stated rather than derived - a house, a car, a pension
    reserve - since the page already draws that history for any asset
    and only a property could record one; property DETAILS only where
    there is a property;
    positions only on an investment account no bank connection feeds,
    since a connected one has its holdings rewritten by every sync; the
    lien link only on a debt; remove only where no connection feeds it (a
    connected account belongs to its bank connection; one whose bank was
    disconnected is the user's again). Close or Reopen, and Hide or
    Show, by the state given (a frontend that gives none offers neither;
    a pot is never shown). Both frontends map these keys to their labels.
    """
    actions = ["rename", "institution", "reconcile"]
    if account_type == PROPERTY_ACCOUNT_TYPE:
        actions.append("property")
    if account_type in STATED_VALUE_ACCOUNT_TYPES:
        actions.append("valuations")
    # Positions only where a provider does not keep them: a SnapTrade
    # account's holdings are rewritten on every sync, so a hand-typed row
    # would be overwritten by the next one and read as data loss.
    if account_type in INVESTMENT_ACCOUNT_TYPES and not connected:
        actions.append("positions")
    if classification == "liability":
        actions.append("terms")
        actions.append("secured_by")
    # Any account can be the one a duplicate folds into (#309).
    actions.append("merge")
    if closed is not None:
        actions.append("reopen" if closed else "close")
    if hidden is not None and account_type not in POT_ACCOUNT_TYPES:
        actions.append("show" if hidden else "hide")
    if not connected:
        actions.append("remove")
    return tuple(actions)
