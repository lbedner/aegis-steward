"""
Two-column shell for the Overseer's auth surfaces (Login, Register).

The shell handles everything that's common to those views — the
top-left brand mark, the right-side branding panel, the bottom-left
footer, the responsive 5/7 column split — and slots a caller-supplied
``form_block`` into the centered position on the left column.

Keeps ``LoginView`` and ``RegisterView`` focused on their own fields +
submit logic.
"""

from __future__ import annotations

import flet as ft

from app.components.frontend.controls.auth.branding_panel import BrandingPanel
from app.components.frontend.controls.brand_mark import BrandMark
from app.components.frontend.styles import PulseColors


class AuthShell(ft.ResponsiveRow):
    """Two-column auth shell. Wrap a form_block with it."""

    def __init__(
        self,
        *,
        form_block: ft.Control,
        project_name: str,
        project_tagline: str,
        footer: ft.Control | None = None,
    ) -> None:
        brand = BrandMark(project_name=project_name)

        # Footer is optional — caller supplies one (e.g. cross-link to the
        # other auth view). When omitted, we still hold the bottom slot
        # with a zero-height container so the form stays vertically
        # positioned the same way.
        footer_widget = footer or ft.Container()

        left = ft.Container(
            content=ft.Column(
                controls=[
                    brand,
                    ft.Container(expand=1),
                    form_block,
                    ft.Container(expand=2),
                    footer_widget,
                ],
                expand=True,
                horizontal_alignment=ft.CrossAxisAlignment.START,
                spacing=0,
            ),
            col={"xs": 12, "md": 5, "lg": 5, "xl": 4},
            bgcolor=PulseColors.BG,
            padding=ft.padding.symmetric(horizontal=64, vertical=48),
            expand=True,
        )

        right = ft.Container(
            content=BrandingPanel(project_name=project_name, tagline=project_tagline),
            col={"xs": 0, "md": 7, "lg": 7, "xl": 8},
            bgcolor=PulseColors.BG,
            expand=True,
        )

        super().__init__(
            controls=[left, right],
            spacing=0,
            run_spacing=0,
            expand=True,
        )
