"""
Register view for the Overseer.

Mirrors ``LoginView``'s shape — same ``AuthShell`` chrome — and the form
column composes the four registration fields (full name, email, password,
confirm password) plus a submit. POSTs ``/api/v1/auth/register`` via
``APIClient.request_with_status`` so the caller can branch on:

- ``200``  — registered. The backend has already set the HttpOnly
              session cookie on the response; we just navigate to ``/``.
- ``400``  — email already registered (validation failure).
- ``403``  — registration disabled by config (``REGISTRATION_ENABLED``).
- ``422``  — schema validation failure (password too short, bad email).
- otherwise — generic error surfaced via ``ErrorSnackBar``.
"""

from __future__ import annotations

from typing import Any

import flet as ft

from app.components.frontend.controls.auth import AuthShell
from app.components.frontend.controls.buttons import PulseButton
from app.components.frontend.controls.form_fields import FormTextField
from app.components.frontend.controls.snack_bar import (
    ErrorSnackBar,
    SuccessSnackBar,
)
from app.components.frontend.controls.text import (
    DisplayText,
    SecondaryText,
)
from app.components.frontend.controls.views.base import BaseView
from app.components.frontend.core.routes import (
    DASHBOARD_ROUTE,
    LOGIN_ROUTE,
)
from app.components.frontend.state.session_state import get_session_state
from app.components.frontend.styles import PulseColors
from app.core.log import logger

_PROJECT_NAME = "aegis-steward"
_PROJECT_TAGLINE = "A production-ready async Python application built with Aegis Stack"
_FORM_MAX_WIDTH = 360


class RegisterView(BaseView):
    """Full-bleed registration surface."""

    def __init__(self, *, page: ft.Page, route: str) -> None:
        super().__init__(
            page=page,
            route=route,
            padding=0,
            bgcolor=PulseColors.BG,
            spacing=0,
        )

        # ---- form fields & buttons ----
        self._full_name = FormTextField(
            label="Name",
            hint="Jane Doe",
            autofocus=True,
            variant="pulse",
            width=_FORM_MAX_WIDTH,
        )
        self._email = FormTextField(
            label="Email",
            hint="you@domain.com",
            keyboard_type=ft.KeyboardType.EMAIL,
            variant="pulse",
            width=_FORM_MAX_WIDTH,
        )
        self._password = FormTextField(
            label="Password",
            hint="At least 8 characters",
            password=True,
            can_reveal_password=True,
            variant="pulse",
            width=_FORM_MAX_WIDTH,
        )
        self._confirm = FormTextField(
            label="Confirm password",
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
            text="Create account",
            on_click_callable=self._handle_submit,
        )
        self._submit.width = _FORM_MAX_WIDTH

        # ---- form column ----
        form_block = ft.Column(
            controls=[
                DisplayText("Create your account", color=PulseColors.TEXT),
                SecondaryText(
                    "Get started in a few seconds.",
                    color=PulseColors.MUTED,
                ),
                ft.Container(height=16),
                self._full_name,
                self._email,
                self._password,
                self._confirm,
                self._error,
                self._submit,
            ],
            spacing=14,
            tight=True,
            horizontal_alignment=ft.CrossAxisAlignment.START,
        )

        # ---- footer: back to sign in ----
        footer = ft.Row(
            controls=[
                SecondaryText(
                    "Already have an account?",
                    color=PulseColors.MUTED,
                    size=12,
                ),
                ft.TextButton(
                    text="Sign in",
                    on_click=self._goto_login,
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
        return None

    async def on_refresh(self) -> None:
        return None

    # ---- handlers ----

    def _goto_login(self, _: Any) -> None:
        self.page.go(LOGIN_ROUTE)

    async def _handle_submit(self, *_: Any) -> None:
        full_name = self._full_name.value.strip() or None
        email = self._email.value.strip()
        password = self._password.value
        confirm = self._confirm.value

        if not email or not password:
            self._show_error("Email and password are required.")
            return
        if len(password) < 8:
            self._show_error("Password must be at least 8 characters.")
            return
        if password != confirm:
            self._show_error("Passwords do not match.")
            return

        self._submit.disabled = True
        self._error.visible = False
        self._submit.update()
        self._error.update()

        api = get_session_state(self.page).api_client
        body: dict[str, Any] = {"email": email, "password": password}
        if full_name:
            body["full_name"] = full_name

        try:
            status, payload = await api.request_with_status(
                "POST", "/api/v1/auth/register", json=body
            )
        except Exception as exc:
            logger.warning("auth.register.network_error", error=str(exc))
            ErrorSnackBar("Could not reach the server.").launch(self.page)
            self._submit.disabled = False
            self._submit.update()
            return

        if status == 200 or status == 201:
            # Backend set the HttpOnly session cookie on the register
            # response — the APIClient's jar already has it. Navigate
            # straight to the dashboard.
            SuccessSnackBar("Welcome aboard.").launch(self.page)
            logger.info(
                "auth.register.success",
                session_id=self.page.session_id,
            )
            self.page.go(DASHBOARD_ROUTE)
            return

        if status == 400:
            detail = self._extract_detail(payload, "Email already registered.")
            self._show_error(detail)
        elif status == 403:
            self._show_error(self._extract_detail(payload, "Registration is disabled."))
        elif status == 422:
            self._show_error(self._extract_detail(payload, "Please check your inputs."))
        elif status == 0:
            ErrorSnackBar("Could not reach the server.").launch(self.page)
        else:
            self._show_error(
                self._extract_detail(payload, f"Registration failed (status {status}).")
            )

        self._submit.disabled = False
        self._submit.update()

    @staticmethod
    def _extract_detail(payload: Any, fallback: str) -> str:
        if isinstance(payload, dict):
            detail = payload.get("detail")
            if isinstance(detail, str) and detail:
                return detail
            # FastAPI 422 returns a list of error dicts.
            if isinstance(detail, list) and detail:
                first = detail[0]
                if isinstance(first, dict):
                    msg = first.get("msg")
                    if isinstance(msg, str) and msg:
                        return msg
        return fallback

    def _show_error(self, message: str) -> None:
        self._error.value = message
        self._error.visible = True
        self._error.update()
