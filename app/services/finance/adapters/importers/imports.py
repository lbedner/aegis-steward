"""Import pipeline: batch bookkeeping + two-lane transaction dedup.

Shared by every importer (OFX/QFX, QIF, CSV). Each run creates a
``finance_import_batch`` — short-circuiting an identical re-upload by
``file_sha256`` — writes one ``finance_import_batch_row`` per record, and
inserts new transactions while counting duplicates. Writes but does not commit
(the caller owns the transaction boundary).

``finance_import_batch`` / ``_row`` carry a NOT-NULL ``owner_user_id``; in
standalone (no-auth) mode the owner is ``None``, so it's coerced to the ``0``
sentinel for those two tables (transactions stay nullable).

Matching runs three lanes, most authoritative first: a provider id
(LANE 1), a content hash (LANE 2), and finally (account, date, amount)
(LANE 3), which absorbs an EDIT made in the source app - a renamed payee
or re-categorized charge updates the existing row instead of landing as
a second copy of the same money.

Classification is a PURE READ, split into ``plan_transactions``: it decides
every row's outcome (insert / duplicate / update / skip / error) without
writing anything. ``ingest_transactions`` executes a plan; ``preview_file``
returns one untouched — so what the preview shows is by construction what a
commit would do, not a parallel re-implementation that can drift.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime
import hashlib
import time
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.clock import utcnow
from app.core.log import logger
from app.services.finance.adapters.importers import queries
from app.services.finance.adapters.importers.base import (
    CATEGORY_KEPT_NOTE,
    IGNORED_REASONS,
    ImportPlan,  # noqa: F401 — re-export; declare.py and the service facade say imports.ImportPlan
    ImportResult,
    ParsedTransaction,
    PlannedRow,
    UnsupportedFileTypeError,  # noqa: F401 — re-export; the API router catches imports.UnsupportedFileTypeError
    _extension,
    _is_posted,
    _parse_by_extension,
    assign_import_hashes,
    infer_account_kind,  # noqa: F401 — re-export; the account-kind tests read it here
)
from app.services.finance.adapters.importers.plan import (
    plan_transactions,
)
from app.services.finance.models import (
    FinanceImportBatch,
    FinanceImportBatchRow,
    FinanceImportProfile,
    FinanceTransactionTag,
)
from app.services.finance.utils import current_date
from app.services.system.jobs import SetLabel, unwatched

# What a source is CALLED where a person reads it. The stored value is
# the machine's word (``snaptrade_sync``, ``csv``), constrained by the
# batch table itself; this is the answer to "where did this come from",
# which is the question actually being asked.
SOURCE_LABELS: dict[str, str] = {
    "snaptrade_sync": "SnapTrade",
    "plaid_sync": "Plaid",
    "csv": "CSV",
    "ofx": "OFX",
    "qfx": "QFX",
    "qif": "QIF",
    "manual": "By hand",
}


def run_title(batch: Any) -> str:
    """What to call one run: where it came from, and the file if it had
    one. Beside ``run_summary`` because a list of runs and a single run
    must not name the same row two ways."""
    source = getattr(batch, "source_type", "") or ""
    named = SOURCE_LABELS.get(source, source)
    file_name = getattr(batch, "file_name", None)
    return f"{named} · {file_name}" if file_name else named


# What a run is counted in, in the order a reader wants it. The counts a
# FILE has live on the batch; the ones only a provider has (holdings,
# trades) ride in ``detail`` - which is the whole reason ``detail`` is a
# JSON column and not five more.
RUN_COUNTS: tuple[tuple[str, str], ...] = (
    ("rows_inserted", "new"),
    ("rows_updated", "updated"),
    ("holdings", "holdings"),
    ("trades", "trades"),
    ("rows_duplicate", "duplicate"),
    ("rows_error", "failed"),
)


def run_summary(batch: Any) -> str:
    """What one run actually brought, in its own terms.

    A file counts rows; a brokerage counts holdings and trades. Reading
    "0 transactions" off a sync that restated ten positions is how a
    working import gets reported as broken, so each source answers in the
    units it deals in.

    Lives here rather than in a page because the answer is a fact about
    the run: the Activity list, a run dialog, the CLI and anything else
    that reports one must not each invent their own wording for the same
    five numbers.
    """
    detail = getattr(batch, "detail", None) or {}
    said = [
        f"{count} {word}"
        for attr, word in RUN_COUNTS
        for count in [getattr(batch, attr, None) or detail.get(attr) or 0]
        if count
    ]
    if said:
        return " · ".join(said)
    # A run that brought nothing is the most interesting kind, so it says
    # so rather than rendering an empty cell.
    return "nothing" if getattr(batch, "status", "") == "committed" else "-"


async def _prior_batch(
    db: AsyncSession, *, batch_owner: int, file_sha256: str
) -> FinanceImportBatch | None:
    """The batch that already ingested these exact bytes, if any."""
    return await queries.prior_batch(
        db, batch_owner=batch_owner, file_sha256=file_sha256
    )


def _identical_result(prior: FinanceImportBatch) -> ImportResult:
    """An identical-bytes submission changes nothing: the prior batch IS
    the outcome, reported all-duplicate."""
    return ImportResult(
        batch_id=prior.id,
        rows_total=prior.rows_total,
        rows_duplicate=prior.rows_total,
    )


# One heartbeat per this many rows: nine lines for an 18,618-row file,
# enough to see it moving without turning the log into the ledger.
PROGRESS_EVERY_ROWS = 2000

# How often the loop drains what it has made. Deliberately NOT the same
# constant as the heartbeat above, which is a reading cadence: changing
# how chatty the log is must not change how much memory the run takes.
#
# Every row adds a FinanceImportBatchRow. ``create_transaction`` flushes,
# so an insert-heavy file drains itself by accident - but a re-import of
# a file the ledger already holds takes the duplicate ``continue`` before
# any of that, and never queries either, so autoflush never fires and the
# whole file sat in the session. 18,607 rows held 51.3 MiB unflushed
# against 2.0 MiB flushed (measured 2026-09-19).
FLUSH_EVERY_ROWS = 2000


async def ingest_transactions(
    db: AsyncSession,
    *,
    owner_user_id: int | None,
    source_type: str,
    file_name: str | None,
    file_bytes: bytes,
    parsed: list[ParsedTransaction],
    default_account_id: int | None = None,
    import_profile_id: int | None = None,
    auto_create_accounts: bool = False,
    on_label: SetLabel | None = None,
) -> ImportResult:
    """Ingest parsed transactions under a reversible, deduped import batch.

    Plans first (``plan_transactions``, a pure read), then executes the plan:
    minting the accounts and categories it named, applying updates, inserting
    rows, and writing one batch row per record. ``auto_create_accounts``
    routes each row to an account named by its ``account_key`` (a
    multi-account CSV), creating one when absent. Otherwise rows use
    ``default_account_id`` (single-account) or provider-id matching.
    """
    batch_owner = 0 if owner_user_id is None else owner_user_id
    file_sha256 = hashlib.sha256(file_bytes).hexdigest()
    # Resolved once so the three places this run speaks are three lines,
    # not three ``if``s around the same question.
    say = on_label or unwatched
    # Every stage below logs against this. The incident these lines were
    # written for (2026-09-19) was a worker SIGKILLed mid-import: with
    # nothing logged, a killed job and a slow one look identical.
    started = time.monotonic()

    # Identical re-upload short-circuit: return the prior batch, all-duplicate.
    prior = await _prior_batch(db, batch_owner=batch_owner, file_sha256=file_sha256)
    if prior is not None:
        logger.info(
            "finance.import.identical_reupload",
            file_name=file_name,
            batch_id=prior.id,
        )
        return _identical_result(prior)

    batch = FinanceImportBatch(
        owner_user_id=batch_owner,
        source_type=source_type,
        file_name=file_name,
        file_sha256=file_sha256,
        import_profile_id=import_profile_id,
        status="processing",
        rows_total=len(parsed),
        started_at=utcnow(),
    )
    try:
        async with db.begin_nested():
            db.add(batch)
            await db.flush()
    except IntegrityError:
        # A concurrent submission of the same bytes won the insert between
        # our check and our flush (a double-clicked Import, confirmed
        # live). Land on the winner's batch instead of crashing the job.
        prior = await _prior_batch(db, batch_owner=batch_owner, file_sha256=file_sha256)
        if prior is None:
            raise
        return _identical_result(prior)

    from app.services.finance.service import FinanceService

    service = FinanceService(db)
    # Before the call, not after: planning is where the time and the
    # memory go (most of the wall clock for an 18,607-row file, measured
    # 2026-09-19) and it runs before a single row is written. A label
    # that arrives afterwards describes a wait already spent.
    await say(f"Checking {len(parsed):,} rows against your ledger...")
    plan = await plan_transactions(
        db,
        owner_user_id=owner_user_id,
        parsed=parsed,
        default_account_id=default_account_id,
        auto_create_accounts=auto_create_accounts,
    )
    # ``rows_to_edit`` is what the plan had to load as transactions; the
    # lanes it compared against are columns and are not held. It was
    # every live row on the account until 2026-09-19, which is the whole
    # reason this line reports it.
    logger.info(
        "finance.import.planned",
        file_name=file_name,
        source_type=source_type,
        batch_id=batch.id,
        rows_total=len(parsed),
        rows_to_edit=len(plan.existing_by_id),
        new_accounts=len(plan.new_accounts),
        elapsed_s=round(time.monotonic() - started, 2),
    )

    # Mint the accounts the plan named, then restamp the placeholder
    # groups' hashes with the real ids (ordinals are deterministic, so
    # only the account component of the hash changes).
    real_account_id: dict[int, int] = {}
    if plan.new_accounts:
        placeholder_groups: dict[int, list[ParsedTransaction]] = defaultdict(list)
        for row in plan.rows:
            if row.account_id is not None and row.account_id < 0:
                placeholder_groups[row.account_id].append(row.txn)
        for key, placeholder in plan.account_by_key.items():
            if placeholder is None or placeholder >= 0 or key is None:
                continue
            account_type, classification = plan.new_accounts[key]
            created = await service.create_manual_account(
                owner_user_id=owner_user_id,
                name=key,
                account_type=account_type,
                classification=classification,
            )
            real_account_id[placeholder] = created.id
        for placeholder, group in placeholder_groups.items():
            assign_import_hashes(group, account_id=real_account_id[placeholder])

    def _actual_account(row: PlannedRow) -> int | None:
        if row.account_id is not None and row.account_id < 0:
            return real_account_id[row.account_id]
        return row.account_id

    # Memoize category resolution: an import typically repeats a small set
    # of category strings across many rows.
    category_cache: dict[str | None, int | None] = {}

    async def _category_for(hint: str | None) -> int | None:
        if hint not in category_cache:
            category_id = await service.resolve_category_alias(hint)
            if category_id is None and hint:
                # Unknown category names are the USER'S OWN curation (e.g. a
                # Quicken tree like "Bills & Utilities:Streaming"); dropping
                # them silently discards it. Create category + alias instead.
                category = await service.get_or_create_category_from_hint(hint)
                category_id = category.id if category is not None else None
            category_cache[hint] = category_id
        return category_cache[hint]

    # A payee already named on this ledger lands already named, keyed on
    # the same four-token grouping the user confirmed when they named it.
    # A key they have since taught a second payee stops resolving; see
    # ``_remember_payee_keys``.
    #
    # Resolved for the WHOLE file in one query rather than memoized per
    # descriptor the way categories are: an import repeats a handful of
    # category strings across thousands of rows, but descriptors are
    # nearly all distinct (the card tail and date vary per swipe), so a
    # cache would miss on almost every row and turn this into the N+1
    # ``test_ingest_query_count_is_flat_in_row_count`` exists to catch.
    merchant_by_descriptor = await service.resolve_merchant_aliases(
        [r.txn.original_description or r.txn.name for r in plan.rows],
        owner_user_id=owner_user_id,
    )

    # What each of those payees is normally filed under. A file's own
    # category column is a guess made somewhere else; the payee's
    # default is the user's standing decision about this payee, so it
    # wins. Live case: an Anthropic subscription arrived from a CSV as
    # "Food & Dining:Groceries" while the payee itself said
    # "Bills & Utilities:Productivity" and four earlier rows agreed.
    payee_default_category = await service.merchant_default_categories(
        set(merchant_by_descriptor.values())
    )

    # Memoize tag rows the same way (Quicken tags repeat heavily).
    tag_cache: dict[str, int] = {}

    async def _tag_ids_for(raw: str | None) -> list[int]:
        if not raw:
            return []
        ids: list[int] = []
        for part in raw.split(","):
            tag_name = part.strip()
            if not tag_name:
                continue
            if tag_name not in tag_cache:
                tag = await service.get_or_create_tag(
                    tag_name, owner_user_id=owner_user_id
                )
                tag_cache[tag_name] = tag.id
            ids.append(tag_cache[tag_name])
        return ids

    async def _apply_edit(row: PlannedRow) -> str:
        """Apply a planned in-place update to the matched transaction.

        The plan decided WHAT changes (including the user-category guard);
        this only performs it. Date and amount are the match key and never
        change here. Returns a human-readable summary of what changed -
        stored on the batch row so an edit is auditable (and reversible by
        hand) rather than a silent overwrite.
        """
        txn = row.txn
        existing = plan.existing_by_id[row.matched_transaction_id]
        changes: list[str] = []
        for field_name, current, incoming in row.field_changes:
            changes.append(f"{field_name}: {current!r} -> {incoming!r}")
            setattr(existing, field_name, incoming)
        if row.category_action == "set":
            category_id = await _category_for(txn.category_hint)
            if category_id is not None and category_id != existing.category_id:
                changes.append(
                    f"category_id: {existing.category_id!r} -> {category_id!r}"
                )
                existing.category_id = category_id
        elif row.category_action == "kept":
            changes.append(CATEGORY_KEPT_NOTE)
        if row.category_stamps_rule and existing.category_source == "unset":
            existing.category_source = "rule"
        # The content hash is derived from the fields just overwritten, so
        # it must be restamped - otherwise the NEXT import sees an unknown
        # hash and re-enters this same path forever.
        if txn.import_hash is not None:
            existing.import_hash = txn.import_hash
            existing.within_day_ordinal = txn.within_day_ordinal

        if row.tags_changed:
            tag_ids = await _tag_ids_for(txn.tags)
            if tag_ids:
                current_tags = await queries.tag_links_for_transaction(db, existing.id)
                if {t.tag_id for t in current_tags} != set(tag_ids):
                    for link in current_tags:
                        await db.delete(link)
                    for tag_id in tag_ids:
                        db.add(
                            FinanceTransactionTag(
                                transaction_id=existing.id, tag_id=tag_id
                            )
                        )
                    changes.append("tags updated")
        # A preserved user category is a decision worth recording, but not
        # a mutation - only real field changes restamp updated_at.
        if [c for c in changes if c != CATEGORY_KEPT_NOTE]:
            existing.updated_at = utcnow()
            db.add(existing)
        return "; ".join(changes)

    inserted = updated = duplicate = error = skipped = ignored = 0
    created_id_by_row: dict[int, int] = {}
    for done, row in enumerate(plan.rows, 1):
        txn = row.txn
        # A heartbeat, not a metric: an 18,618-row file spent a minute in
        # this loop looking exactly like a hung process, and the browser's
        # spinner had nothing better to go on either.
        if done % PROGRESS_EVERY_ROWS == 0:
            logger.info(
                "finance.import.progress",
                file_name=file_name,
                batch_id=batch.id,
                rows_done=done,
                rows_total=len(plan.rows),
                inserted=inserted,
                duplicate=duplicate,
                elapsed_s=round(time.monotonic() - started, 2),
            )
            # Counts, not a percentage: "4,000 of 18,607" says how much is
            # left AND that the numbers are moving, and the two the user
            # cares about are what landed and what was already here.
            await say(
                f"Importing {done:,} of {len(plan.rows):,} - "
                f"{inserted:,} added, {duplicate:,} already there"
            )
        if done % FLUSH_EVERY_ROWS == 0:
            # Write what this chunk made and let go of it. Still one
            # transaction - the caller owns the commit, so a failure
            # anywhere still takes the whole import back out.
            await db.flush()
        if row.status == "skipped":
            if row.reason in IGNORED_REASONS:
                ignored += 1
            else:
                skipped += 1
            db.add(
                FinanceImportBatchRow(
                    import_batch_id=batch.id,
                    owner_user_id=batch_owner,
                    row_number=row.row_number,
                    parsed_status="skipped",
                    reason=row.reason,
                    content_hash=txn.import_hash,
                    fitid=txn.external_id,
                )
            )
            continue
        if row.status == "error":
            error += 1
            db.add(
                FinanceImportBatchRow(
                    import_batch_id=batch.id,
                    owner_user_id=batch_owner,
                    row_number=row.row_number,
                    parsed_status="error",
                    reason=row.reason,
                    content_hash=txn.import_hash,
                    fitid=txn.external_id,
                )
            )
            continue

        account_id = _actual_account(row)
        if row.status == "duplicate":
            duplicate += 1
            matched = row.matched_transaction_id
            if matched is None and row.duplicate_of_row is not None:
                matched = created_id_by_row.get(row.duplicate_of_row)
            db.add(
                FinanceImportBatchRow(
                    import_batch_id=batch.id,
                    owner_user_id=batch_owner,
                    account_id=account_id,
                    row_number=row.row_number,
                    parsed_status="duplicate",
                    matched_transaction_id=matched,
                    content_hash=txn.import_hash,
                    fitid=txn.external_id,
                )
            )
            continue

        if row.status == "updated":
            summary = await _apply_edit(row)
            updated += 1
            db.add(
                FinanceImportBatchRow(
                    import_batch_id=batch.id,
                    owner_user_id=batch_owner,
                    account_id=account_id,
                    row_number=row.row_number,
                    parsed_status="updated",
                    matched_transaction_id=row.matched_transaction_id,
                    reason=summary or "no field changed",
                    content_hash=txn.import_hash,
                    fitid=txn.external_id,
                )
            )
            continue

        merchant_id = merchant_by_descriptor.get(
            txn.original_description or txn.name or ""
        )
        category_id = payee_default_category.get(
            merchant_id or 0
        ) or await _category_for(txn.category_hint)
        created = await service.create_transaction(
            owner_user_id=owner_user_id,
            account_id=account_id,
            amount=txn.amount,
            txn_date=txn.date,
            name=txn.name,
            source=txn.source,
            external_id=txn.external_id,
            external_id_source=txn.external_id_source,
            import_hash=txn.import_hash,
            within_day_ordinal=txn.within_day_ordinal,
            import_batch_id=batch.id,
            raw_amount=txn.raw_amount,
            raw_sign_convention=txn.raw_sign_convention,
            original_description=txn.original_description,
            memo=txn.memo,
            check_number=txn.check_number,
            category_id=category_id,
            category_source="rule" if category_id is not None else "unset",
            merchant_id=merchant_id,
            is_split=bool(txn.splits),
        )
        created_id_by_row[row.row_number] = created.id
        for sort_order, split in enumerate(txn.splits):
            await service.create_split(
                parent_transaction_id=created.id,
                owner_user_id=owner_user_id,
                amount=split.amount,
                category_id=await _category_for(split.category_hint),
                memo=split.memo,
                sort_order=sort_order,
            )
        for tag_id in await _tag_ids_for(txn.tags):
            db.add(FinanceTransactionTag(transaction_id=created.id, tag_id=tag_id))
        inserted += 1
        db.add(
            FinanceImportBatchRow(
                import_batch_id=batch.id,
                owner_user_id=batch_owner,
                account_id=account_id,
                row_number=row.row_number,
                parsed_status="inserted",
                matched_transaction_id=created.id,
                content_hash=txn.import_hash,
                fitid=txn.external_id,
            )
        )

    # If the file carried a running balance (e.g. a Quicken register's Balance
    # column), set the target account's ``current_balance`` from the latest
    # row — so net worth reflects the import without a separate valuation.
    if default_account_id is not None:
        # Posted rows only: a scheduled row's running balance is a
        # PROJECTED figure, and taking it as the account's real balance
        # would book money that has not moved.
        today = current_date()
        balanced = [
            (txn.date, i, txn.running_balance)
            for i, txn in enumerate(parsed)
            if _is_posted(txn, today) and txn.running_balance is not None
        ]
        if balanced:
            balanced.sort(key=lambda item: (item[0], item[1]))
            ending_date, _, ending_balance = balanced[-1]
            account = await queries.account_ref(db, default_account_id)
            if account is not None:
                account.current_balance = ending_balance
                account.balance_as_of = datetime(
                    ending_date.year, ending_date.month, ending_date.day
                )
                db.add(account)

    batch.rows_inserted = inserted
    batch.rows_updated = updated
    # No rows_skipped column on the batch: the skipped rows are recorded
    # individually with parsed_status="skipped" and a reason, so the count
    # stays queryable without a migration.
    batch.rows_duplicate = duplicate
    batch.rows_error = error
    batch.status = "committed"
    batch.finished_at = utcnow()
    db.add(batch)
    await db.flush()

    # Reconcile the freshly imported rows: pair internal transfers (so a
    # card payment doesn't double-count as spend), detect recurring streams,
    # and generate "wasting money" insights.
    # An edit can re-categorize a charge or rename a payee, which changes
    # what the rules see - so reconcile after updates too, not only inserts.
    if inserted or updated:
        from app.services.finance.domains.detection import (
            detect_recurring,
            detect_transfers,
            generate_insights,
            promote_curated_streams,
        )

        # The detection pass is its own wait (transfers, recurring,
        # insights over the whole ledger) and the row counter has stopped
        # by now, so silence here reads as a stall.
        await say("Reconciling transfers, bills and insights...")
        logger.info(
            "finance.import.reconciling",
            file_name=file_name,
            batch_id=batch.id,
            inserted=inserted,
            updated=updated,
            elapsed_s=round(time.monotonic() - started, 2),
        )
        await detect_transfers(db, owner_user_id=owner_user_id)
        await detect_recurring(db, owner_user_id=owner_user_id)
        # Before the insight rules: a stream the user's own categorization
        # marks as a bill must pass the missed-payment commitment gate on
        # this very pass, not the next one.
        await promote_curated_streams(db, owner_user_id=owner_user_id)
        await generate_insights(db, owner_user_id=owner_user_id)

    logger.info(
        "finance.import.finished",
        file_name=file_name,
        batch_id=batch.id,
        rows_total=len(parsed),
        inserted=inserted,
        updated=updated,
        duplicate=duplicate,
        error=error,
        skipped=skipped,
        ignored=ignored,
        elapsed_s=round(time.monotonic() - started, 2),
    )
    return ImportResult(
        batch_id=batch.id,
        rows_total=len(parsed),
        rows_inserted=inserted,
        rows_updated=updated,
        rows_duplicate=duplicate,
        rows_error=error,
        rows_skipped=skipped,
        rows_ignored=ignored,
    )


def _detect_csv(
    file_bytes: bytes, profiles: list[FinanceImportProfile]
) -> tuple[FinanceImportProfile | None, int]:
    from app.services.finance.adapters.importers import csv_profiles

    return csv_profiles.detect_profile(file_bytes, profiles)


async def _csv_profiles(db: AsyncSession) -> list[FinanceImportProfile]:
    return await queries.csv_profiles(db)


async def import_csv(
    db: AsyncSession,
    *,
    owner_user_id: int | None,
    file_name: str | None,
    file_bytes: bytes,
    account_id: int | None = None,
    on_label: SetLabel | None = None,
) -> ImportResult:
    """Detect the CSV layout from the seeded profiles, parse, and ingest.

    A profile that maps an ``account`` column (e.g. a Quicken "All Transactions"
    report) routes rows to per-name accounts and ignores ``account_id``; every
    other layout imports into the single ``account_id`` (required). On an unknown
    header a ``failed`` batch (zero rows) is recorded and
    ``UnknownCsvLayoutError`` is raised (the API surfaces it as 422).
    """
    from app.services.finance.adapters.importers import csv_profiles

    profiles = await _csv_profiles(db)
    profile, header_index = _detect_csv(file_bytes, profiles)
    if profile is None:
        header = csv_profiles.header_preview(file_bytes)
        batch_owner = 0 if owner_user_id is None else owner_user_id
        # No file hash on a failed batch: the hash dedups files that were
        # ingested (uq_finance_importbatch_file), and carrying it here made
        # the second try of the same unknown bytes an IntegrityError.
        failed = FinanceImportBatch(
            owner_user_id=batch_owner,
            source_type="csv",
            file_name=file_name,
            file_sha256=None,
            status="failed",
            rows_total=0,
            error=f"Unknown CSV layout; header {header}",
            started_at=utcnow(),
            finished_at=utcnow(),
        )
        db.add(failed)
        # Commit the failed batch before raising: get_async_db rolls the session
        # back on any exception, so a bare flush would discard this row and the
        # batch_id handed to the caller would reference nothing. Only the failed
        # batch is pending here, so this commit persists just that row.
        await db.commit()
        raise csv_profiles.UnknownCsvLayoutError(
            header, [p.name for p in profiles], batch_id=failed.id
        )

    parsed = await asyncio.to_thread(
        csv_profiles.parse_csv, file_bytes, profile, header_index=header_index
    )
    multi_account = "account" in profile.column_mapping.values()
    if not multi_account and account_id is None:
        raise ValueError("CSV import requires a target account_id for this layout.")
    return await ingest_transactions(
        db,
        owner_user_id=owner_user_id,
        source_type="csv",
        file_name=file_name,
        file_bytes=file_bytes,
        parsed=parsed,
        default_account_id=None if multi_account else account_id,
        import_profile_id=profile.id,
        auto_create_accounts=multi_account,
        on_label=on_label,
    )


async def import_file(
    db: AsyncSession,
    *,
    owner_user_id: int | None,
    file_name: str | None,
    file_bytes: bytes,
    account_id: int | None = None,
    on_label: SetLabel | None = None,
) -> ImportResult:
    """Dispatch by file extension and ingest.

    ``.ofx``/``.qfx`` -> OFX (account resolvable from the file); ``.qif`` needs
    an explicit ``account_id``; ``.csv`` needs one unless the detected profile
    routes rows by an account column. Unknown extensions raise
    ``UnsupportedFileTypeError`` (HTTP 415).
    """
    if _extension(file_name) == "csv":
        # Single-account layouts still require account_id; import_csv enforces
        # it after detecting the profile (a multi-account layout self-routes).
        return await import_csv(
            db,
            owner_user_id=owner_user_id,
            file_name=file_name,
            file_bytes=file_bytes,
            account_id=account_id,
            on_label=on_label,
        )
    source_type, parsed = await asyncio.to_thread(
        _parse_by_extension, file_name, file_bytes
    )
    if source_type == "qif" and account_id is None:
        raise ValueError("QIF import requires a target account_id.")
    return await ingest_transactions(
        db,
        owner_user_id=owner_user_id,
        source_type=source_type,
        file_name=file_name,
        file_bytes=file_bytes,
        parsed=parsed,
        default_account_id=account_id,
        on_label=on_label,
    )


async def get_import_batch(
    db: AsyncSession, batch_id: int, *, owner_user_id: int | None = None
) -> FinanceImportBatch | None:
    # finance_import_batch.owner_user_id is NOT NULL; standalone uses 0.
    batch_owner = 0 if owner_user_id is None else owner_user_id
    return await queries.import_batch_by_id(db, batch_id, batch_owner=batch_owner)


async def list_import_batches(
    db: AsyncSession,
    *,
    owner_user_id: int | None = None,
    page: int = 1,
    page_size: int = 20,
) -> list[FinanceImportBatch]:
    batch_owner = 0 if owner_user_id is None else owner_user_id
    return await queries.import_batches_page(
        db, batch_owner=batch_owner, page=page, page_size=page_size
    )


async def list_import_batch_rows(
    db: AsyncSession, batch_id: int
) -> list[FinanceImportBatchRow]:
    return await queries.import_batch_rows(db, batch_id)


# The preview lives in its own module; re-exported so callers keep saying
# ``imports.preview_file`` (the API router and the service facade both do).
from app.services.finance.adapters.importers.preview import (  # noqa: E402
    preview_file as preview_file,
)
