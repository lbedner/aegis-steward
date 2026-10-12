"""
Auth Users Tab Component

Displays user management table with actions for enabling/disabling and deleting users.
Fetches real data from the /auth/users API endpoint.
"""

from typing import Any

import flet as ft

from app.components.frontend.controls import (
    ConfirmDialog,
    DataTable,
    DataTableColumn,
    H3Text,
    SecondaryText,
    Tag,
)
from app.components.frontend.controls.snack_bar import (
    ErrorSnackBar,
    SuccessSnackBar,
)
from app.components.frontend.state.session_state import get_session_state
from app.components.frontend.theme import AegisTheme as Theme
from app.core.formatting import format_relative_time
from app.services.system import ui_auth


class UsersTableSection(ft.Container):
    """Users table section with action buttons."""

    def __init__(
        self,
        users: list[dict[str, Any]],
        on_toggle: Any,
        on_delete: Any,
    ) -> None:
        super().__init__()

        columns = [
            DataTableColumn("Email", style="primary"),
            DataTableColumn("Name", width=140, style="secondary"),
            DataTableColumn("Status", width=45, style=None),
            DataTableColumn("Verified", width=55, style=None),
            DataTableColumn("Created", width=80, style="secondary"),
            DataTableColumn("Actions", width=80, alignment="right", style=None),
        ]

        rows: list[list[Any]] = []
        for user in users:
            is_active = user.get("is_active", True)
            status_tag = Tag(
                text="On" if is_active else "Off",
                color=Theme.Colors.SUCCESS if is_active else Theme.Colors.WARNING,
            )

            is_verified = user.get("is_verified", False)
            verified_tag = Tag(
                text="Yes" if is_verified else "No",
                color=Theme.Colors.SUCCESS if is_verified else Theme.Colors.WARNING,
            )

            # Action buttons
            action_buttons = ft.Row(
                [
                    ft.IconButton(
                        icon=ft.Icons.BLOCK if is_active else ft.Icons.CHECK_CIRCLE,
                        icon_size=18,
                        icon_color=ft.Colors.ON_SURFACE_VARIANT,
                        tooltip="Disable" if is_active else "Enable",
                        on_click=lambda e, u=user: on_toggle(u),
                    ),
                    ft.IconButton(
                        icon=ft.Icons.DELETE_OUTLINE,
                        icon_size=18,
                        icon_color=ft.Colors.ON_SURFACE_VARIANT,
                        tooltip="Delete",
                        on_click=lambda e, u=user: on_delete(u),
                    ),
                ],
                spacing=0,
            )

            rows.append(
                [
                    user.get("email", ""),
                    user.get("full_name") or "-",
                    status_tag,
                    verified_tag,
                    format_relative_time(user.get("created_at")),
                    action_buttons,
                ]
            )

        table = DataTable(
            columns=columns,
            rows=rows,
            empty_message="No users found",
            scroll_height=400,
        )

        self.content = table
        self.padding = Theme.Spacing.MD


class AuthUsersTab(ft.Container):
    """
    Users management tab for the Auth Service modal.

    Fetches and displays user list from the API with management actions.
    """

    def __init__(self, metadata: dict[str, Any] | None = None) -> None:
        super().__init__()
        self._metadata = metadata or {}

        # Content container that will be updated after data loads
        self._content_column = ft.Column(
            [
                ft.Container(
                    content=ft.ProgressRing(width=32, height=32),
                    alignment=ft.alignment.center,
                    padding=Theme.Spacing.XL,
                ),
                ft.Container(
                    content=SecondaryText("Loading users..."),
                    alignment=ft.alignment.center,
                ),
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=Theme.Spacing.MD,
        )

        self.content = self._content_column

    def did_mount(self) -> None:
        """Called when the control is added to the page. Fetches data."""
        # Check if database is available
        if not self._metadata.get("database_available", False):
            self._render_unavailable()
        else:
            self.page.run_task(self._load_users)

    async def _load_users(self) -> None:
        """Fetch users from API and update the UI."""
        api = get_session_state(self.page).api_client
        users = await api.get("/api/v1/auth/users")
        if users is None:
            self._render_error("Could not load users.")
            return
        if not isinstance(users, list):
            self._render_error("Unexpected response shape from /auth/users.")
            return

        self._render_users(users)

    def _refresh_row(self) -> ft.Row:
        """Trailing-aligned refresh icon, included on render paths where a
        retry is meaningful (loaded and error states). Skipped on the
        unavailable state since refresh can't restore a missing database."""
        return ft.Row(
            [
                ft.Container(expand=True),
                ft.IconButton(
                    icon=ft.Icons.REFRESH,
                    icon_color=ft.Colors.ON_SURFACE_VARIANT,
                    tooltip="Refresh users",
                    on_click=self._on_refresh_click,
                ),
            ],
            alignment=ft.MainAxisAlignment.END,
        )

    def _render_users(
        self,
        users: list[dict[str, Any]],
    ) -> None:
        """Render the users table with loaded data."""

        self._content_column.controls = [
            self._refresh_row(),
            UsersTableSection(
                users,
                on_toggle=self._on_toggle_user,
                on_delete=self._on_delete_user,
            ),
        ]
        self._content_column.scroll = ft.ScrollMode.AUTO
        self._content_column.spacing = 0
        self.update()

    def _render_error(self, message: str) -> None:
        """Render an error state. The refresh button is included so the user
        can retry after fixing the underlying problem (e.g. role bump)
        without closing and reopening the dialog."""
        self._content_column.controls = [
            self._refresh_row(),
            ft.Container(
                content=ft.Icon(
                    ft.Icons.ERROR_OUTLINE,
                    size=48,
                    color=Theme.Colors.ERROR,
                ),
                alignment=ft.alignment.center,
                padding=Theme.Spacing.MD,
            ),
            ft.Container(
                content=H3Text("Failed to load users"),
                alignment=ft.alignment.center,
            ),
            ft.Container(
                content=SecondaryText(message),
                alignment=ft.alignment.center,
            ),
        ]
        self._content_column.horizontal_alignment = ft.CrossAxisAlignment.CENTER
        self.update()

    def _render_unavailable(self) -> None:
        """Render unavailable state when database is not available."""
        self._content_column.controls = [
            ft.Container(height=40),
            ft.Icon(
                ft.Icons.PEOPLE_OUTLINE,
                size=64,
                color=ft.Colors.OUTLINE,
            ),
            ft.Container(height=Theme.Spacing.MD),
            H3Text("User Management Unavailable"),
            ft.Container(height=Theme.Spacing.SM),
            SecondaryText("Database backend required for user management."),
        ]
        self._content_column.horizontal_alignment = ft.CrossAxisAlignment.CENTER
        self._content_column.alignment = ft.MainAxisAlignment.START
        self.update()

    async def _on_refresh_click(self, e: ft.ControlEvent) -> None:
        """Handle refresh button click."""
        self._content_column.controls = [
            ft.Container(
                content=ft.ProgressRing(width=32, height=32),
                alignment=ft.alignment.center,
                padding=Theme.Spacing.XL,
            ),
            ft.Container(
                content=SecondaryText("Refreshing..."),
                alignment=ft.alignment.center,
            ),
        ]
        self._content_column.horizontal_alignment = ft.CrossAxisAlignment.CENTER
        self._content_column.spacing = Theme.Spacing.MD
        self.update()

        await self._load_users()

    def _on_toggle_user(self, user: dict[str, Any]) -> None:
        """Handle enable/disable user button click."""
        page = self.page
        if not page:
            return

        is_active = user.get("is_active", True)
        action = "deactivate" if is_active else "activate"
        action_text = "Disable" if is_active else "Enable"
        user_id = user["id"]

        async def do_toggle() -> None:
            await self._toggle_user(action, user_id)

        ConfirmDialog(
            page=page,
            title=f"{action_text} User",
            message=f"{action_text} user '{user.get('email')}'?",
            confirm_text=action_text,
            on_confirm=do_toggle,
        ).show()

    def _on_delete_user(self, user: dict[str, Any]) -> None:
        """Handle delete user button click."""
        page = self.page
        if not page:
            return

        user_id = user["id"]

        async def do_delete() -> None:
            await self._delete_user(user_id)

        title, message = ui_auth.delete_user_confirmation(user.get("email", ""))
        ConfirmDialog(
            page=page,
            title=title,
            message=message,
            confirm_text="Delete",
            on_confirm=do_delete,
            destructive=True,
        ).show()

    async def _toggle_user(self, action: str, user_id: int) -> None:
        """Enable or disable a user via API."""
        api = get_session_state(self.page).api_client
        status, body = await api.request_with_status(
            "PATCH", f"/api/v1/auth/users/{user_id}/{action}"
        )

        if status == 200:
            verb = "enabled" if action == "activate" else "disabled"
            SuccessSnackBar(f"User {verb}").launch(self.page)
            await self._load_users()
        elif status == 400:
            ErrorSnackBar("Cannot modify your own account.").launch(self.page)
        else:
            detail = (
                body.get("detail")
                if isinstance(body, dict) and body.get("detail")
                else f"status {status}"
            )
            ErrorSnackBar(f"Update failed: {detail}").launch(self.page)

    async def _delete_user(self, user_id: int) -> None:
        """Delete a user via API."""
        api = get_session_state(self.page).api_client
        status, body = await api.request_with_status(
            "DELETE", f"/api/v1/auth/users/{user_id}"
        )

        if status == 204:
            SuccessSnackBar("User deleted").launch(self.page)
            await self._load_users()
        elif status == 400:
            ErrorSnackBar("Cannot delete your own account.").launch(self.page)
        else:
            detail = (
                body.get("detail")
                if isinstance(body, dict) and body.get("detail")
                else f"status {status}"
            )
            ErrorSnackBar(f"Delete failed: {detail}").launch(self.page)
