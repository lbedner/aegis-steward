"""Finance service constants: provider keys, ciphertext column registry.

Enum-style value sets that back ``String`` + ``CheckConstraint`` columns are
added here as their tables land. Kept as plain string constants (not native
DB enums) so adding a value is a normal migration on both SQLite and Postgres.
"""

import calendar
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Literal, NamedTuple

SERVICE_NAME = "finance"

# Dashboard/health component identifier (mirrors PAYMENT_COMPONENT_NAME).
FINANCE_COMPONENT_NAME = "finance"

# The analyst agent's daily note rides the insight table but is not a finding:
# it never counts toward the anomaly badge, never appears in the Insights list,
# and is never fed back to the agent as something to explain. Lives here rather
# than in the analyst module so the service layer can exclude it without
# importing (or requiring) the AI service.
ANALYST_NOTE_INSIGHT_TYPE = "analyst_note"

# Named rows in an import review's detail sections before the tail folds
# into a count. A Quicken tree can carry hundreds of new categories, and a
# review that scrolls for a page stops being read at all.
PREVIEW_DETAIL_CAP = 10


class Provider:
    """Connection providers. ``manual`` always ships; the rest are flag-gated."""

    PLAID = "plaid"
    SNAPTRADE = "snaptrade"
    MANUAL = "manual"


# Encrypted (AES-GCM ciphertext) columns on ``finance_connection``. Registered
# here so key-rotation tooling can find every finance secret. Encryption /
# decryption happens in the service layer with a row-bound AAD context
# ``finance_connection:{id}:{column}``.
ENCRYPTED_COLUMNS: tuple[str, ...] = (
    "access_token_encrypted",
    "api_key_encrypted",
    "api_secret_encrypted",
    "api_passphrase_encrypted",
    "refresh_token_encrypted",
)


# Accounts that bills actually draw from. Investments and property have
# balances too, but "can I cover the month" is a question about cash. Shared
# by the balance projection, the liquidity insight rules, and the analyst
# snapshot so "cash on hand" means the same thing everywhere it appears.
CASH_ACCOUNT_TYPES: frozenset[str] = frozenset(
    {"checking", "savings", "cash", "money_market"}
)


# Names a source app uses for "I did not classify this". A row can be
# uncategorized two ways: no category at all, or one of these buckets
# carried in by an import. Checking only for NULL reports zero
# uncategorized on a Quicken import that has over a thousand of them.
UNCATEGORIZED_CATEGORY_NAMES = frozenset(
    {"uncategorized", "unclassified", "other income", "misc", "miscellaneous"}
)


# --- Cadences -------------------------------------------------------------
#
# THE table. A cadence carries six independent facts, and they used to live
# in six hand-written maps across five modules: detection's canonical gaps,
# the forecast's step functions, the menus' labels, the create/update
# validator, the API schema's Literal, and the monthly-equivalent weights.
#
# They drifted, repeatedly, and every gap was found by a user rather than a
# test: the forecast could step semiannual before detection could name it,
# so a six-month insurance premium measured as "irregular" and disappeared
# from the projection; the menus were then extended but the validator was
# not, so the dropdown 422'd; the schema was missed after that, so the fix
# never reached the endpoint.
#
# Adding a cadence is now one entry here. Everything else derives.
#
# ``irregular`` and ``unknown`` are deliberately NOT in this table: they are
# what a stream stores when no cadence fits (or there is only one
# occurrence). They can be stored - the column's CheckConstraint allows them
# - but they cannot be stepped, offered in a menu, or weighed in a rollup,
# which is exactly the difference this table encodes.


@dataclass(frozen=True)
class Cadence:
    """One recurring interval, and everything that depends on it.

    ``detect_days`` is the canonical gap a measured median is matched
    against; it doubles as the ordering key. ``months`` steps by calendar
    month (so Jan 31 + 1 month is Feb 28, not Mar 3); ``days`` steps by a
    fixed count. Exactly one of the two is set.
    """

    label: str
    detect_days: int
    months: int = 0
    days: int = 0
    monthly_factor: float = 1.0
    # Days past due before a stream counts as missed. Short cadences get a
    # tighter window: a weekly charge four days late is meaningful, a
    # monthly one is not.
    grace_days: int = 5


# Ordered SHORTEST FIRST, and the order is load-bearing: a measured median
# is matched against the first band it falls in, so where two bands touch
# the shorter cadence takes the overlap.
CADENCES: dict[str, Cadence] = {
    "weekly": Cadence("Weekly", 7, days=7, monthly_factor=52 / 12, grace_days=3),
    "biweekly": Cadence(
        "Every 2 weeks", 14, days=14, monthly_factor=26 / 12, grace_days=3
    ),
    "semi_monthly": Cadence("Twice a month", 15, days=15, monthly_factor=2.0),
    "monthly": Cadence("Monthly", 30, months=1, monthly_factor=1.0),
    "bimonthly": Cadence("Every 2 months", 60, months=2, monthly_factor=0.5),
    "quarterly": Cadence("Quarterly", 90, months=3, monthly_factor=1 / 3),
    "semi_annually": Cadence("Every 6 months", 180, months=6, monthly_factor=1 / 6),
    "annually": Cadence("Yearly", 365, months=12, monthly_factor=1 / 12),
}

CADENCE_KEYS: tuple[str, ...] = tuple(CADENCES)

# The same set as a type, for request schemas. Built FROM the table rather
# than retyped: a schema listing six cadences while the service stored
# eight is how a fix at the service layer never reached the endpoint.
# Spelled out because a computed ``Literal`` is invisible to type checkers
# and to the OpenAPI schema. It is NOT a second source of truth:
# ``test_the_api_schema_accepts_exactly_these`` fails the moment this and
# ``CADENCES`` disagree, which is the drift that let the endpoint reject
# cadences the service was happy to store.
CadenceKey = Literal[
    "weekly",
    "biweekly",
    "semi_monthly",
    "monthly",
    "bimonthly",
    "quarterly",
    "semi_annually",
    "annually",
    "once",
]

# Stored when nothing fits. Not cadences: nothing can step them.
IRREGULAR_FREQUENCY = "irregular"
UNKNOWN_FREQUENCY = "unknown"

# A bill with a date but no rhythm - "pay Bob back on the 15th". Not a
# cadence either (there is no next occurrence to step to), but unlike
# irregular/unknown it is USER-STATED and fully forecastable: it projects
# exactly one occurrence and contributes nothing to any monthly rollup.
# An indefinite pause. NOT null - a null ``paused_until`` already means
# "not paused" in every consumer, so indefinite is a date that never
# arrives: comparisons, endpoints and serialization all work unchanged,
# and only display code needs to know (see pause_label).
PAUSE_INDEFINITE = date(9999, 12, 31)

ONE_TIME_FREQUENCY = "once"
ONE_TIME_LABEL = "One time"

# Menu text for every cadence, derived from the one table so no picker
# can offer something create/update would reject.
FREQUENCY_LABELS: dict[str, str] = {
    key: cadence.label for key, cadence in CADENCES.items()
}

# What the add/edit bill forms offer: the cadences plus "One time" - a
# dated debt ("pay Bob back on the 15th") is a bill, not a rhythm, so it
# is statable here but never appears in cadence-only surfaces (detection,
# the declare-from-transactions picker).
BILL_FREQUENCY_OPTIONS: dict[str, str] = {
    **FREQUENCY_LABELS,
    ONE_TIME_FREQUENCY: ONE_TIME_LABEL,
}

# Frequencies a stream can carry that detection never produces and nobody
# would pick from a menu: "irregular" is a measured gap matching no
# cadence, "unknown" a bill with one transaction and no gap to measure.
DECLARED_FREQUENCY_LABELS: dict[str, str] = {
    IRREGULAR_FREQUENCY: "Irregular",
    UNKNOWN_FREQUENCY: "Not enough history yet",
    ONE_TIME_FREQUENCY: ONE_TIME_LABEL,
}


def frequency_label(key: str | None) -> str:
    """Display text for any stored frequency; an unknown key reads as itself."""
    if not key:
        return ""
    return FREQUENCY_LABELS.get(key) or DECLARED_FREQUENCY_LABELS.get(key) or key


def add_months(day: date, months: int) -> date:
    """Calendar-aware month step (Jan 31 + 1 month = Feb 28, not Mar 3)."""
    month_index = day.month - 1 + months
    year = day.year + month_index // 12
    month = month_index % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def step_cadence(key: str, day: date) -> date:
    """The next occurrence after ``day`` at cadence ``key``."""
    cadence = CADENCES[key]
    if cadence.months:
        return add_months(day, cadence.months)
    return day + timedelta(days=cadence.days)


# ``external_id_source`` value marking a reconciliation adjustment (FIN-37).
# A plain-column discriminator: the import pipeline's LANE-3 edit matching
# and the source CHECK constraint both stay untouched by it.
RECONCILE_MARKER = "reconcile"


# How accounts group in any account list (sidebar, filter, report), in
# display order. Keyed by ``account_type``; unknown types fall into "Other".
ACCOUNT_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Banking", ("checking", "savings", "cash")),
    ("Credit Cards", ("credit_card",)),
    ("Investments", ("investment", "brokerage", "crypto")),
    ("Property", ("property", "vehicle")),
    ("Loans & Debt", ("loan", "other_liability")),
    ("Other", ("other_asset",)),
)

PROPERTY_ACCOUNT_TYPE = "property"

# Account types whose detail view is holdings (positions), not transactions.
INVESTMENT_ACCOUNT_TYPES = frozenset({"brokerage", "investment", "crypto"})

# Assets whose worth is STATED rather than derived. A checking balance
# comes from its transactions and a brokerage's from its holdings, so a
# hand-typed value history would be a second answer to a question the
# ledger already answers. A house, a car and a pension reserve have no
# such source: somebody reads a figure off a statement or a listing, and
# what it was last year is worth keeping.
STATED_VALUE_ACCOUNT_TYPES = frozenset({"property", "vehicle", "other_asset"})


def account_group(account_type: str) -> str:
    for label, types in ACCOUNT_GROUPS:
        if account_type in types:
            return label
    return "Other"


def account_sections(accounts: Sequence[Any]) -> list[tuple[str, list[Any]]]:
    """``(label, accounts)`` in ACCOUNT_GROUPS order, empty groups
    dropped.

    One bucketing for every surface that shows accounts in sections: the
    Accounts page reads it for its groups and the account filter reads
    it for its own, so a new account type appears in both or neither.
    Takes anything with an ``account_type`` - a model row or a response
    - because both sides of the app ask the same question.
    """
    buckets: dict[str, list[Any]] = {}
    for account in accounts:
        buckets.setdefault(account_group(account.account_type), []).append(account)
    return [
        (label, buckets[label]) for label, _types in ACCOUNT_GROUPS if label in buckets
    ]


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
    "remove": AccountThing("Remove"),
}

# The menu labels, derived. Both frontends import this name.
ACCOUNT_ACTION_LABELS: dict[str, str] = {
    key: thing.action for key, thing in ACCOUNT_THINGS.items()
}


def account_actions(
    *, account_type: str, classification: str, is_manual: bool
) -> tuple[str, ...]:
    """What a UI may offer to do to an account, in menu order.

    Rename, institution and reconcile always; what it is WORTH wherever
    the worth is stated rather than derived - a house, a car, a pension
    reserve - since the page already draws that history for any asset
    and only a property could record one; property DETAILS only where
    there is a property;
    positions only on a MANUAL investment account, since a connected
    one has its holdings rewritten by every sync; the lien link only on
    a debt; remove only for a manual account (a provider account belongs
    to its bank connection). Both frontends map these keys to their
    labels.
    """
    actions = ["rename", "institution", "reconcile"]
    if account_type == PROPERTY_ACCOUNT_TYPE:
        actions.append("property")
    if account_type in STATED_VALUE_ACCOUNT_TYPES:
        actions.append("valuations")
    # Positions only where a provider does not keep them: a SnapTrade
    # account's holdings are rewritten on every sync, so a hand-typed row
    # would be overwritten by the next one and read as data loss.
    if account_type in INVESTMENT_ACCOUNT_TYPES and is_manual:
        actions.append("positions")
    if classification == "liability":
        actions.append("terms")
        actions.append("secured_by")
    if is_manual:
        actions.append("remove")
    return tuple(actions)


# Curated (account_type, label) choices for a manual "Add account" form. Keys
# are the DB-constrained account_type values; classification derives below.
ADD_ACCOUNT_TYPES: tuple[tuple[str, str], ...] = (
    ("checking", "Checking"),
    ("savings", "Savings"),
    ("cash", "Cash"),
    ("credit_card", "Credit card"),
    ("loan", "Loan"),
    ("brokerage", "Brokerage"),
    ("crypto", "Crypto"),
    ("property", "Property"),
    ("vehicle", "Vehicle"),
    ("other_asset", "Other asset"),
    ("other_liability", "Other liability"),
)
LIABILITY_ACCOUNT_TYPES = frozenset({"credit_card", "loan", "other_liability"})


def account_classification(account_type: str) -> str:
    return "liability" if account_type in LIABILITY_ACCOUNT_TYPES else "asset"


def account_tag(account_id: int) -> str:
    """The one label that files a document against an account.

    The document service says outright that what a tag MEANS differs per
    application and the framework has no business guessing, so this is
    steward's meaning, written once: the change type that files a
    document and the page that lists them both read it from here.
    """
    return f"account:{account_id}"


def tagged_account(tag: str) -> int | None:
    """The account an ``account_tag`` names, or None for any other tag.
    Beside it so the format is written in one place."""
    prefix, _, rest = tag.partition(":")
    return int(rest) if f"{prefix}:" == account_tag(0)[:-1] and rest.isdigit() else None
