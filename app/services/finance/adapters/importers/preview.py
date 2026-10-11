"""The import dry run: what ``import_file`` WOULD do, as a pure read.

Split from ``imports`` so the commit pipeline and the preview each fit in
a reader's head. Same dispatch, same classification, nothing written.
"""

from __future__ import annotations

import asyncio
from datetime import date
import hashlib

from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.finance.adapters.importers import imports
from app.services.finance.adapters.importers.base import ParsedTransaction
from app.services.finance.adapters.importers.imports import (
    _csv_profiles,
    _detect_csv,
    _extension,
    _parse_by_extension,
    _prior_batch,
)
from app.services.finance.adapters.importers.plan import ImportPlan, plan_transactions
from app.services.finance.domains.ledger.queries import accounts as account_queries
from app.services.finance.domains.ledger.queries import categories as category_queries
from app.services.finance.models import FinanceAccount
from app.services.finance.schemas import ImportPreviewEdit, ImportPreviewResponse
from app.services.shared.queries import stored_owner


def _asks_for_account(
    parsed: list[ParsedTransaction], *, file_name: str | None, layout: str
) -> ImportPlan:
    """The preview's answer to a single-account file with no target: not
    a 400 the client can only echo, but a plan that says which account is
    missing so the client can ask for it and preview again."""
    return ImportPlan(
        rows=[],
        parsed=parsed,
        account_by_key={},
        new_accounts={},
        new_category_hints=[],
        existing_by_id={},
        file_name=file_name,
        rows_total=len(parsed),
        needs_account=True,
        layout=layout,
    )


async def preview_file(
    db: AsyncSession,
    *,
    owner_user_id: int | None,
    file_name: str | None,
    file_bytes: bytes,
    account_id: int | None = None,
) -> ImportPlan:
    """What ``import_file`` WOULD do, as a pure read.

    Same dispatch, same account requirements, same classification — via the
    same ``plan_transactions`` the commit executes — but nothing is written:
    no batch (not even the failed-batch record on an unknown CSV layout — the
    ``UnknownCsvLayoutError`` still raises), no accounts, no categories.
    An exact-bytes re-upload returns a plan carrying ``identical_batch_id``
    and no rows: importing it again would change nothing.
    """
    batch_owner = stored_owner(owner_user_id)
    file_sha256 = hashlib.sha256(file_bytes).hexdigest()
    prior = await _prior_batch(db, batch_owner=batch_owner, file_sha256=file_sha256)
    if prior is not None:
        return ImportPlan(
            rows=[],
            parsed=[],
            account_by_key={},
            new_accounts={},
            new_category_hints=[],
            existing_by_id={},
            file_name=file_name,
            rows_total=prior.rows_total,
            identical_batch_id=prior.id,
        )

    if _extension(file_name) == "csv":
        from app.services.finance.adapters.importers import csv_profiles

        profiles = await _csv_profiles(db)
        profile, header_index = _detect_csv(file_bytes, profiles)
        if profile is None:
            raise csv_profiles.UnknownCsvLayoutError(
                csv_profiles.header_preview(file_bytes),
                [p.name for p in profiles],
                batch_id=None,
            )
        parsed = await asyncio.to_thread(
            csv_profiles.parse_csv, file_bytes, profile, header_index=header_index
        )
        multi_account = "account" in profile.column_mapping.values()
        layout = profile.name
        if not multi_account and account_id is None:
            return _asks_for_account(parsed, file_name=file_name, layout=layout)
        plan = await plan_transactions(
            db,
            owner_user_id=owner_user_id,
            parsed=parsed,
            default_account_id=None if multi_account else account_id,
            auto_create_accounts=multi_account,
        )
    else:
        source_type, parsed = await asyncio.to_thread(
            _parse_by_extension, file_name, file_bytes
        )
        layout = source_type.upper()
        if source_type == "qif" and account_id is None:
            return _asks_for_account(parsed, file_name=file_name, layout=layout)
        plan = await plan_transactions(
            db,
            owner_user_id=owner_user_id,
            parsed=parsed,
            default_account_id=account_id,
        )
    plan.layout = layout
    if account_id is not None:
        account = await db.get(FinanceAccount, account_id)
        plan.account_name = account.name if account is not None else None
    plan.file_name = file_name
    return plan


async def preview_payload(db: AsyncSession, plan: ImportPlan) -> ImportPreviewResponse:
    """Shape an ImportPlan for the client: counts, would-create lists, and
    per-edit change strings with category NAMES (ids mean nothing to the
    person deciding whether to press Import)."""
    if plan.identical_batch_id is not None:
        return ImportPreviewResponse(
            file_name=plan.file_name,
            rows_total=plan.rows_total,
            identical_batch_id=plan.identical_batch_id,
            rows_duplicate=plan.rows_total,
        )
    if plan.needs_account:
        return ImportPreviewResponse(
            file_name=plan.file_name,
            rows_total=plan.rows_total,
            needs_account=True,
            layout=plan.layout,
        )

    # One pass for the names the display needs: real accounts touched by
    # the plan, and every category id an edit mentions.
    account_ids = {
        row.account_id
        for row in plan.rows
        if row.account_id is not None and row.account_id > 0
    }
    account_names = await account_queries.account_names(db, list(account_ids))
    category_ids = {
        cid
        for row in plan.rows
        if row.status == "updated"
        for cid in (row.category_current_id, row.category_new_id)
        if cid is not None
    }
    category_names = await category_queries.category_names_by_id(db, category_ids)

    def _account_label(row: imports.PlannedRow) -> str:
        if row.account_id is not None and row.account_id > 0:
            return account_names.get(row.account_id, f"account {row.account_id}")
        return f"{row.account_key or 'unresolved'} (new)"

    inserts_by_account: dict[str, int] = {}
    insert_dates: list[date] = []
    edits: list[ImportPreviewEdit] = []
    kept = 0
    for row in plan.rows:
        if row.status == "inserted":
            label = _account_label(row)
            inserts_by_account[label] = inserts_by_account.get(label, 0) + 1
            if row.txn.date is not None:
                insert_dates.append(row.txn.date)
            continue
        if row.status != "updated":
            continue
        existing = plan.existing_by_id[row.matched_transaction_id]
        changes = [
            f"{field}: {current!r} -> {incoming!r}"
            for field, current, incoming in row.field_changes
        ]
        if row.category_action == "set":
            current_name = category_names.get(row.category_current_id, "Uncategorized")
            new_name = (
                category_names.get(row.category_new_id)
                if row.category_new_id is not None
                else f"{row.txn.category_hint} (new)"
            )
            changes.append(f"category: {current_name!r} -> {new_name!r}")
        elif row.category_action == "kept":
            changes.append(imports.CATEGORY_KEPT_NOTE)
            kept += 1
        if row.tags_changed:
            changes.append("tags updated")
        edits.append(
            ImportPreviewEdit(
                transaction_id=existing.id,
                date=existing.date_,
                amount=existing.amount,
                name=existing.name,
                account=_account_label(row),
                changes=changes or ["no field changed"],
                category_kept=row.category_action == "kept",
            )
        )

    return ImportPreviewResponse(
        file_name=plan.file_name,
        rows_total=plan.rows_total,
        layout=plan.layout,
        account_name=plan.account_name,
        rows_inserted=plan.count("inserted"),
        rows_updated=plan.count("updated"),
        rows_duplicate=plan.count("duplicate"),
        rows_error=plan.count("error"),
        rows_skipped=sum(
            1
            for row in plan.rows
            if row.status == "skipped" and row.reason not in imports.IGNORED_REASONS
        ),
        rows_ignored=sum(
            1
            for row in plan.rows
            if row.status == "skipped" and row.reason in imports.IGNORED_REASONS
        ),
        removed_accounts=plan.removed_accounts,
        insert_date_start=min(insert_dates, default=None),
        insert_date_end=max(insert_dates, default=None),
        inserts_by_account=inserts_by_account,
        new_accounts=sorted(plan.new_accounts),
        new_categories=sorted(set(plan.new_category_hints)),
        edits=edits,
        category_kept_count=kept,
    )
