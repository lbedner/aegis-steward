"""
Card Container Component

Custom container component for dashboard cards following the TechBadge pattern.
Provides consistent styling, hover effects, and click handling for all dashboard cards.
"""

import flet as ft

from app.services.system.models import ComponentStatus

from .card_utils import create_card_click_handler


class CardContainer(ft.Container):
    """
    A standardized card container with consistent styling and hover effects.

    Features:
    - Consistent styling (border, radius, bgcolor, dimensions)
    - Hover effect (border 1px→3px with negative padding compensation)
    - Click handling with modal integration
    - Status-aware border coloring
    """

    def __init__(
        self,
        content: ft.Row,
        border_color: str,
        component_data: ComponentStatus,
        component_name: str,
        width: int | None = None,
    ) -> None:
        """
        Initialize the card container.

        Args:
            content: The main row content of the card
            border_color: Border color for the card
            component_data: ComponentStatus for modal integration
            component_name: Name of component for modal routing
            width: Optional width override (None = responsive)
        """
        self._original_border_color = border_color
        self.component_data = component_data
        self.component_name = component_name

        super().__init__(
            content=content,
            bgcolor=ft.Colors.SURFACE,
            border=ft.border.all(1, border_color),
            border_radius=16,
            padding=0,
            animate=ft.Animation(200, ft.AnimationCurve.EASE_OUT),
            width=width,
            height=350,
            on_hover=self._handle_hover,
            on_click=self._handle_click,
        )

        # Set cursor after initialization
        self.cursor = ft.MouseCursor.CLICK

    def _handle_hover(self, e: ft.ControlEvent) -> None:
        """Handle hover by increasing border thickness without shifting content."""
        if e.data == "true":  # Mouse enter
            self.border = ft.border.all(3, self._original_border_color)
            self.padding = ft.padding.all(-2)  # Compensate for border increase
            self.elevation = 2
        else:  # Mouse leave
            self.border = ft.border.all(1, self._original_border_color)
            self.padding = ft.padding.all(0)
            self.elevation = 0
        self.update()

    def _handle_click(self, e: ft.ControlEvent) -> None:
        """Handle card click by opening detail modal."""
        handler = create_card_click_handler(self.component_name, self.component_data)
        handler(e)
