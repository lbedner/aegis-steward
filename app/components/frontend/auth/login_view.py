"""
Login view for the Overseer.

Composes the form inline in ``__init__`` — no ``_build_*`` methods, the
layout is read top-to-bottom. The two-column shell (brand mark, branding
panel, footer) is delegated to ``AuthShell`` so this file can stay
focused on the sign-in fields and submit logic.

Auth flow:
- Submit POSTs ``/api/v1/auth/token`` via ``APIClient.post_form``.
- On 200, the backend issues an HttpOnly ``<project>_session`` cookie that
  lands in the per-session ``APIClient``'s cookie jar automatically.
  We just navigate to ``/``; the router's auth guard then permits the
  dashboard via a ``/auth/me`` round-trip.
- On 401 / network failure, an ``ErrorSnackBar`` surfaces the reason and
  the form re-enables.
"""

from __future__ import annotations

from typing import Any

import flet as ft

from app.components.frontend.controls.auth import AuthShell
from app.components.frontend.controls.buttons import PulseButton
from app.components.frontend.controls.form_fields import FormTextField
from app.components.frontend.controls.snack_bar import ErrorSnackBar
from app.components.frontend.controls.text import (
    DisplayText,
    SecondaryText,
)
from app.components.frontend.controls.views.base import BaseView
from app.components.frontend.core.routes import (
    DASHBOARD_ROUTE,
    REGISTER_ROUTE,
)
from app.components.frontend.state.session_state import get_session_state
from app.components.frontend.styles import PulseColors
from app.core.config import settings
from app.core.log import logger

_PROJECT_NAME = "aegis-steward"
_PROJECT_TAGLINE = "A production-ready async Python application built with Aegis Stack"
_FORM_MAX_WIDTH = 360


class LoginView(BaseView):
    """Full-bleed sign-in surface."""

    def __init__(self, *, page: ft.Page, route: str) -> None:
        super().__init__(
            page=page,
            route=route,
            padding=0,
            bgcolor=PulseColors.BG,
            spacing=0,
        )

        # ---- form fields & buttons ----
        self._email = FormTextField(
            label="Email",
            hint="you@domain.com",
            keyboard_type=ft.KeyboardType.EMAIL,
            autofocus=True,
            variant="pulse",
            width=_FORM_MAX_WIDTH,
        )
        self._password = FormTextField(
            label="Password",
            hint="••••••••",
            password=True,
            can_reveal_password=True,
            variant="pulse",
            width=_FORM_MAX_WIDTH,
            on_submit=self._handle_submit,
        )
        self._error = ft.Text(
            value="",
            color="#E94E77",
            size=12,
            visible=False,
        )
        self._submit = PulseButton(
            text="Sign in",
            on_click_callable=self._handle_submit,
        )
        self._submit.width = _FORM_MAX_WIDTH

        # ---- form column ----
        form_block = ft.Column(
            controls=[
                DisplayText("Welcome back", color=PulseColors.TEXT),
                SecondaryText("Sign in to your dashboard.", color=PulseColors.MUTED),
                ft.Container(height=16),
                self._email,
                self._password,
                self._error,
                self._submit,
            ],
            spacing=14,
            tight=True,
            horizontal_alignment=ft.CrossAxisAlignment.START,
        )

        # ---- footer: register link, while signups are open ----
        footer = (
            None
            if not settings.REGISTRATION_ENABLED
            else ft.Row(
                controls=[
                    SecondaryText(
                        "New here?",
                        color=PulseColors.MUTED,
                        size=12,
                    ),
                    ft.TextButton(
                        text="Create an account",
                        on_click=self._goto_register,
                        style=ft.ButtonStyle(
                            color=PulseColors.TEAL,
                            padding=ft.padding.all(0),
                            text_style=ft.TextStyle(size=12),
                        ),
                    ),
                ],
                spacing=6,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            )
        )

        self.controls = [
            AuthShell(
                form_block=form_block,
                project_name=_PROJECT_NAME,
                project_tagline=_PROJECT_TAGLINE,
                footer=footer,
            )
        ]

    # ---- lifecycle ----

    async def on_enter(self, params: dict[str, Any]) -> None:
        # Surface any session-expiry hint passed via query param.
        if params.get("reason") == "expired":
            ErrorSnackBar("Your session expired. Sign in again.").launch(self.page)

    async def on_refresh(self) -> None:
        # Login is stateless — nothing to reload.
        return None

    # ---- handlers ----

    def _goto_register(self, _: Any) -> None:
        self.page.go(REGISTER_ROUTE)

    async def _handle_submit(self, *_: Any) -> None:
        # Called from both ``PulseButton`` (no args) and
        # ``ft.TextField.on_submit`` (single ControlEvent arg).
        email = self._email.value.strip()
        password = self._password.value
        if not email or not password:
            self._show_error("Email and password are required.")
            return

        self._submit.disabled = True
        self._error.visible = False
        self._submit.update()
        self._error.update()

        api = get_session_state(self.page).api_client
        try:
            payload = await api.post_form(
                "/api/v1/auth/token",
                {"username": email, "password": password},
            )
        except Exception as exc:
            logger.warning("auth.login.network_error", error=str(exc))
            ErrorSnackBar("Could not reach the server.").launch(self.page)
            self._submit.disabled = False
            self._submit.update()
            return

        if not payload or not isinstance(payload, dict):
            self._show_error("Invalid email or password.")
            self._submit.disabled = False
            self._submit.update()
            return

        # The session cookie is now in the APIClient's jar; navigate
        # and let the router's auth guard verify it via ``/auth/me``.
        logger.info("auth.login.success", session_id=self.page.session_id)
        self.page.go(DASHBOARD_ROUTE)

    def _show_error(self, message: str) -> None:
        self._error.value = message
        self._error.visible = True
        self._error.update()
