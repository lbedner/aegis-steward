"""Where does this file go: the import flow's target-account dialogs.

A register statement names no account and a custodian ledger names no
brokerage; both ask the user before anything is written. Split from
``imports_flow`` so each mixin fits in a reader's head. The dialogs know
nothing of what happens next: the caller hands them the continuation.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import flet as ft

from app.components.frontend.controls.buttons import PulseButton
from app.components.frontend.controls.dialog import DialogHandle, StyledAlertDialog
from app.components.frontend.controls.form_fields import FormDropdown, FormTextField
from app.components.frontend.controls.loading_overlay import LoadingOverlay
from app.components.frontend.controls.snack_bar import ErrorSnackBar
from app.components.frontend.controls.text import SecondaryText
from app.components.frontend.dashboard.modals.finance_modal.constants import (
    _NEW_ACCOUNT_KEY,
)
from app.components.frontend.dashboard.modals.finance_modal.import_summary import (
    _suggested_account_name,
    account_target_options,
    investment_import_preview_body,
    investment_target_options,
)
from app.components.frontend.dashboard.modals.finance_modal.transactions_panel.base import (
    TransactionsPanelState,
)
from app.components.frontend.theme import AegisTheme as Theme
from app.services.finance.service import NEEDS_ACCOUNT

PREVIEW_PATH = "/api/v1/finance/import/preview"


class ImportTargetMixin(TransactionsPanelState):
    """The two "into which account?" dialogs of the import flow, and the
    one way the flow hands a file to the server."""

    async def _upload_job(
        self,
        path: str,
        data: bytes,
        original_name: str,
        params: dict[str, object] | None = None,
        *,
        needs_account: Callable[[], Awaitable[None]] | None = None,
    ) -> dict[str, Any] | None:
        """Post the file to an import endpoint and follow the job it
        starts: every one answers with a job id at once and parses on the
        worker (#494), the overlay's label tracking the job's.

        The job's result, or None once the overlay says why not. A file
        that names no account goes to ``needs_account`` instead, when the
        caller can ask for one.
        """
        from app.components.frontend.controls import jobs
        from app.components.frontend.state.session_state import get_session_state

        overlay = LoadingOverlay.of(self.page)
        api = get_session_state(self.page).api_client
        started = await api.post_multipart(
            path,
            files={"file": (original_name, data, "application/octet-stream")},
            params=params or {},
        )
        if not isinstance(started, dict) or "job_id" not in started:
            # The real reason (HTTP status + detail body), not a guess.
            overlay.fail(
                api.last_error or "Import failed for an unknown reason.",
                title="Import failed",
            )
            return None
        outcome = await jobs.follow_job(
            api, started["job_id"], on_label=overlay.update_label
        )
        if outcome.error == NEEDS_ACCOUNT and needs_account is not None:
            overlay.hide()
            await needs_account()
            return None
        if outcome.error is not None:
            overlay.fail(outcome.error, title="Import failed")
            return None
        overlay.hide()
        return outcome.result

    async def _ask_target_account(
        self,
        data: bytes,
        original_name: str,
        then: Callable[..., Awaitable[None]],
    ) -> None:
        """The file names no account and none is selected: ask, then
        preview again with the answer. Explicit beats an error the client
        can only echo, and beats guessing from the sidebar."""
        from app.components.frontend.state.session_state import get_session_state

        api = get_session_state(self.page).api_client
        accounts = await api.get("/api/v1/finance/accounts", cache_ttl=30)
        rows = accounts.get("items", []) if isinstance(accounts, dict) else []
        options, default = account_target_options(rows, None)
        if not options:
            ErrorSnackBar("Create an account first; this file names none.").launch(
                self.page
            )
            return
        target_dd = FormDropdown(label="Into account", options=options, value=default)
        dialog_handle = DialogHandle()

        async def _close() -> None:
            dialog_handle.close(self.page)

        async def _preview_into() -> None:
            account_id = int(target_dd.value or default)
            await _close()
            LoadingOverlay.of(self.page).show(f"Checking {original_name}...")
            again = await self._upload_job(
                PREVIEW_PATH, data, original_name, {"account_id": account_id}
            )
            if again is not None:
                await then(again, data, original_name, account_id=account_id)

        dialog = StyledAlertDialog(
            handle=dialog_handle,
            title=f"Import {original_name}",
            body=ft.Column(
                [
                    SecondaryText(NEEDS_ACCOUNT, size=Theme.Typography.BODY_SMALL),
                    target_dd,
                ],
                spacing=Theme.Spacing.MD,
                tight=True,
            ),
            actions=[
                PulseButton(
                    on_click_callable=_close,
                    text="Cancel",
                    variant="muted",
                    compact=True,
                ),
                PulseButton(
                    on_click_callable=_preview_into,
                    text="Continue",
                    variant="teal",
                    compact=True,
                ),
            ],
            width=480,
        )
        self.page.open(dialog)

    async def _show_investment_import_review(
        self,
        preview: dict,
        accounts: list[dict],
        data: bytes,
        original_name: str,
        commit: Callable[[bytes, str, dict[str, object]], Awaitable[None]],
    ) -> None:
        """The pre-commit review: what the ledger replays to, and where it
        goes - an existing investment account, or one created on the spot
        (the same courtesy OFX ingest extends to unknown accounts)."""
        selected_id = self._account.get("id") if self._account is not None else None
        options, default = investment_target_options(accounts, selected_id)
        name_field = FormTextField(
            label="New account name",
            value=_suggested_account_name(original_name),
        )
        name_host = ft.Container(
            content=name_field, visible=default == _NEW_ACCOUNT_KEY
        )

        def _target_changed(event: ft.ControlEvent) -> None:
            name_host.visible = event.control.value == _NEW_ACCOUNT_KEY
            if name_host.page is not None:
                name_host.update()

        target_dd = FormDropdown(
            label="Into account",
            options=options,
            value=default,
            on_change=_target_changed,
        )
        dialog_handle = DialogHandle()

        async def _close() -> None:
            dialog_handle.close(self.page)

        async def _commit() -> None:
            choice = target_dd.value or _NEW_ACCOUNT_KEY
            params: dict[str, object] = {}
            if choice == _NEW_ACCOUNT_KEY:
                name = (name_field.value or "").strip()
                if not name:
                    name_field.set_error("Name the new account.")
                    return
                params["account_name"] = name
            else:
                params["account_id"] = int(choice)
            await _close()
            await commit(data, original_name, params)

        dialog = StyledAlertDialog(
            handle=dialog_handle,
            title=f"Import {original_name}",
            body=ft.Column(
                [
                    investment_import_preview_body(preview),
                    ft.Container(height=Theme.Spacing.SM),
                    target_dd,
                    name_host,
                ],
                spacing=Theme.Spacing.SM,
                tight=True,
            ),
            actions=[
                PulseButton(
                    on_click_callable=_close,
                    text="Cancel",
                    variant="muted",
                    compact=True,
                ),
                PulseButton(
                    on_click_callable=_commit,
                    text="Import",
                    variant="teal",
                    compact=True,
                ),
            ],
            width=560,
        )
        self.page.open(dialog)
