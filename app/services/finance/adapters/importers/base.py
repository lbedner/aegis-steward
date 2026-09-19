"""The shapes every stage of the import pipeline shares.

Two of them, and the pipeline is the line between: parsers produce
``ParsedTransaction`` records, ``plan`` decides each one's outcome as a
``PlannedRow`` in an ``ImportPlan``, and ``imports`` executes that plan.
The plan shapes live here rather than beside the planner because the
preview endpoint, the declare endpoint and the service facade all read
them without ever calling it.

House rule: amounts are integer minor units and **negative means an
outflow**. ``amount`` is sign-normalized while ``raw_amount`` /
``raw_sign_convention`` preserve the source's original form.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
import hashlib
from typing import Any, NamedTuple

from pydantic import BaseModel, Field

from app.services.finance.models import FinanceTransaction
from app.services.finance.utils import normalize_payee


class ParsedSplit(BaseModel):
    """One split leg of a parsed transaction (category / memo / signed cents)."""

    amount: int
    category_hint: str | None = None
    memo: str | None = None


class ParsedTransaction(BaseModel):
    """A source-agnostic transaction produced by a parser."""

    date: date
    amount: int  # sign-normalized cents (negative = outflow)
    source: str  # 'ofx' | 'qfx' | 'qif' | 'csv'
    external_id: str | None = None
    external_id_source: str | None = None
    import_hash: str | None = None
    within_day_ordinal: int = 0
    raw_amount: int | None = None
    raw_sign_convention: str | None = None
    name: str | None = None
    original_description: str | None = None
    memo: str | None = None
    check_number: str | None = None
    # Running account balance after this row, when the source carries one
    # (e.g. a Quicken register's ``Balance`` column). The pipeline uses the
    # latest-dated value to set the account's ``current_balance``.
    running_balance: int | None = None
    # Free-text category string (e.g. QIF ``L`` value) for alias lookup.
    category_hint: str | None = None
    # Comma-separated tag names (e.g. a Quicken report's Tags column).
    tags: str | None = None
    # A bracketed QIF ``L[Account]`` transfer marker (stored, not paired here).
    transfer_hint: str | None = None
    # The source marks this as a SCHEDULED instance, not posted money (a
    # Quicken "Scheduled"/"Overdue"/"DueToday" row). Such rows are money
    # the user expects to move, not money that moved, so the pipeline
    # records them and moves on rather than putting them in the ledger.
    is_scheduled: bool = False
    splits: list[ParsedSplit] = Field(default_factory=list)
    # Account routing hint (e.g. OFX ACCTID) — resolved against
    # finance_account.provider_account_id, else the explicit account_id param.
    account_key: str | None = None


def compute_import_hash(
    *,
    account_id: int,
    txn_date: date,
    amount_cents: int,
    payee: str | None,
    memo: str | None,
    check_number: str | None,
    within_day_ordinal: int,
) -> str:
    """LANE-2 content hash for id-less rows (QIF/CSV).

    The recipe is a stability contract — changing it silently breaks
    idempotency for every existing import, so it is pinned by a unit test.
    Fields are the account, ISO date, signed cents, normalized payee + memo,
    check number, and the within-day ordinal, joined by ``|``.
    """
    raw = (
        f"{account_id}|{txn_date.isoformat()}|{amount_cents}|"
        f"{normalize_payee(payee)}|{normalize_payee(memo)}|"
        f"{check_number or ''}|{within_day_ordinal}"
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def assign_import_hashes(parsed: list[ParsedTransaction], *, account_id: int) -> None:
    """Set ``within_day_ordinal`` + ``import_hash`` on every LANE-2 row in place.

    Rows are grouped by ``(date, amount, normalized_payee, normalized_memo)``;
    each group is sorted by a DETERMINISTIC key (never file order) and numbered
    ``0..n`` so genuinely-identical rows stay distinct AND a re-export produces
    the same ordinals. Rows that already carry an ``external_id`` (LANE 1) are
    left untouched.
    """
    groups: dict[tuple[date, int, str, str], list[ParsedTransaction]] = defaultdict(
        list
    )
    for txn in parsed:
        if txn.external_id is not None:
            continue
        key = (
            txn.date,
            txn.amount,
            normalize_payee(txn.name),
            normalize_payee(txn.memo),
        )
        groups[key].append(txn)
    for members in groups.values():
        members.sort(key=lambda t: (t.check_number or "", t.memo or "", t.name or ""))
        for ordinal, txn in enumerate(members):
            txn.within_day_ordinal = ordinal
            txn.import_hash = compute_import_hash(
                account_id=account_id,
                txn_date=txn.date,
                amount_cents=txn.amount,
                payee=txn.name,
                memo=txn.memo,
                check_number=txn.check_number,
                within_day_ordinal=ordinal,
            )


class ImportResult(BaseModel):
    """Outcome of an import run (returned to the caller / API)."""

    batch_id: int | None = None
    rows_total: int = 0
    rows_inserted: int = 0
    rows_updated: int = 0
    rows_duplicate: int = 0
    rows_error: int = 0
    # Rows the source carried but that are not posted money (scheduled /
    # future-dated). Recorded on the batch, never put in the ledger.
    rows_skipped: int = 0
    # Rows belonging to an account the user REMOVED - deleting an account
    # is a standing decision, and a re-import must not resurrect it.
    rows_ignored: int = 0


class UnsupportedFileTypeError(ValueError):
    """Raised for a file extension no importer handles."""


def _extension(file_name: str | None) -> str:
    name = (file_name or "").lower()
    return name.rsplit(".", 1)[-1] if "." in name else ""


def _parse_by_extension(
    file_name: str | None, file_bytes: bytes
) -> tuple[str, list[ParsedTransaction]]:
    """(source_type, parsed rows) for OFX/QFX/QIF — the id-carrying formats
    whose parsing needs no DB state. CSV goes through profile detection
    instead. Unknown extensions raise ``UnsupportedFileTypeError``."""
    extension = _extension(file_name)
    if extension in ("ofx", "qfx"):
        from app.services.finance.adapters.importers.ofx import parse_ofx

        return extension, parse_ofx(file_bytes, source=extension)
    if extension == "qif":
        from app.services.finance.adapters.importers.qif import parse_qif

        return "qif", parse_qif(file_bytes, source="qif")
    raise UnsupportedFileTypeError(
        f"Unsupported file type '.{extension}'. Supported: .ofx, .qfx, .qif, .csv."
    )


_ACCOUNT_KIND_RULES: tuple[tuple[tuple[str, ...], str, str], ...] = (
    (("savings",), "savings", "asset"),
    (("checking", "chequing"), "checking", "asset"),
    (("mortgage", "conventional", "fha", "heloc"), "loan", "liability"),
    (("readi cash", "line of credit", " loc ", "loc "), "loan", "liability"),
    (("loan",), "loan", "liability"),
    (
        (
            "amex",
            "american express",
            "visa",
            "mastercard",
            "discover",
            "card",
            "credit",
        ),
        "credit_card",
        "liability",
    ),
    (("401", "403b", "ira", "roth", "pension", "retirement"), "investment", "asset"),
    (("brokerage", "fund", "invest", "etf"), "brokerage", "asset"),
    (("hsa", "fsa"), "other_asset", "asset"),
    (("house", "home", "property", "condo", "real estate"), "property", "asset"),
)


def infer_account_kind(name: str) -> tuple[str, str]:
    """(account_type, classification) guessed from an account name.

    Conservative: only high-confidence keywords match; anything else falls back
    to a generic asset for the user to reclassify. Padded with spaces so short
    tokens like ``loc`` don't match inside unrelated words.
    """
    lowered = f" {(name or '').lower()} "
    for keywords, account_type, classification in _ACCOUNT_KIND_RULES:
        if any(keyword in lowered for keyword in keywords):
            return account_type, classification
    return "other_asset", "asset"


def _is_posted(txn: ParsedTransaction, today: date) -> bool:
    """Money that has moved. A row the source flags as scheduled, or one
    dated in the future, has not — two signals because neither alone is
    enough: Quicken's "Overdue" scheduled rows are dated in the PAST, and
    a source with no scheduled column can still carry future rows."""
    return not (txn.is_scheduled or (txn.date is not None and txn.date > today))


_SKIP_SCHEDULED_REASON = (
    "scheduled: not yet posted. It imports normally once the payment actually clears."
)
_SKIP_REMOVED_REASON = "account was removed"
_SKIP_DELETED_REASON = "transaction was deleted"
# Skip reasons that mean "the user decided this stays out" - counted as
# ignored (not merely skipped) by ingest and the preview payload alike.
IGNORED_REASONS = (_SKIP_REMOVED_REASON, _SKIP_DELETED_REASON)

# The batch-row reason recorded when the LANE-3 edit path would have
# re-categorized a transaction the USER categorized. The user's curation
# outranks the source app's label — see plan's category_action.
CATEGORY_KEPT_NOTE = "category kept (user-set)"


class LaneRow(NamedTuple):
    """One existing transaction, as the dedup lanes actually read it.

    Eight scalar columns, not the entity. The lanes compare ints and
    strings to decide insert/duplicate/update; building a full
    ``FinanceTransaction`` to do that cost 5,345 bytes a row against a
    NamedTuple's 294, and 18,571 of them - 95 MiB - is what took the
    import worker out (2026-09-19). Only a row the plan means to EDIT is
    worth having as a model, and the planner loads exactly those, by id,
    once it knows which they are.

    Built positionally from the select's column order, which is why that
    order lives next to these names and nowhere else.
    """

    id: int
    account_id: int
    source: str
    external_id: str | None
    external_id_source: str | None
    import_hash: str | None
    date_: date
    amount: int


class DeletedLaneRow(NamedTuple):
    """A soft-deleted transaction's lane keys, and nothing else.

    Its own shape rather than a ``LaneRow`` with four dead fields: a
    column that is always None is a question every later reader has to
    answer again.
    """

    account_id: int
    source: str
    external_id: str | None
    import_hash: str | None


class PlannedRow(BaseModel):
    """One parsed row's decided outcome. Computed without writing."""

    row_number: int
    txn: ParsedTransaction
    status: str  # 'inserted' | 'updated' | 'duplicate' | 'skipped' | 'error'
    account_key: str | None = None
    # Negative ids are placeholders for accounts the commit would create
    # (see ImportPlan.new_accounts) — planning cannot mint real rows.
    account_id: int | None = None
    reason: str | None = None
    matched_transaction_id: int | None = None
    # An in-file duplicate of a planned INSERT: the matched transaction id
    # does not exist yet, so the reference is the earlier row's number.
    duplicate_of_row: int | None = None
    # -- 'updated' rows only ------------------------------------------------
    # (field, current, incoming) for the plain label fields.
    field_changes: list[tuple[str, Any, Any]] = Field(default_factory=list)
    # What happens to the category — decided HERE, in one place, so the
    # preview and the commit cannot disagree:
    #   'set'  -> overwrite from the source's category hint
    #   'kept' -> the source disagrees but category_source == 'user';
    #             the user's own categorization is never overwritten
    #   'none' -> no hint, or it resolves to the current category
    category_action: str = "none"
    # The hint resolves (or would create) a category — stamp
    # category_source='rule' on a row that was 'unset', matching the
    # insert path's convention.
    category_stamps_rule: bool = False
    # Resolve-only preview of the category change; None + a hint on the
    # txn means the commit would CREATE the category.
    category_current_id: int | None = None
    category_new_id: int | None = None
    tags_changed: bool = False


class ImportPlan(BaseModel):
    """A read-only classification of parsed rows against the ledger."""

    rows: list[PlannedRow]
    parsed: list[ParsedTransaction]
    account_by_key: dict[str | None, int | None]
    # Account name -> inferred (account_type, classification), for accounts
    # a commit would create (multi-account files only).
    new_accounts: dict[str, tuple[str, str]]
    # Category hints with no alias — a commit creates these (the user's own
    # source-side curation; dropping them silently would discard it).
    new_category_hints: list[str]
    # Existing rows touched by the plan, keyed by id — the commit edits
    # these very objects; the preview reads date/amount/name off them.
    existing_by_id: dict[int, FinanceTransaction]
    file_name: str | None = None
    # Account names the file carries that match a REMOVED account - their
    # rows plan as skipped; deleting an account is a standing decision.
    removed_accounts: list[str] = Field(default_factory=list)
    rows_total: int = 0
    # Set when the exact file bytes were already imported: nothing to do.
    identical_batch_id: int | None = None
    # A single-account layout previewed with no target: the client asks
    # which account the statement belongs to, then previews again.
    needs_account: bool = False
    layout: str | None = None
    account_name: str | None = None

    def count(self, status: str) -> int:
        return sum(1 for row in self.rows if row.status == status)
