"""
Status Overview Panel

Panel showing all Aegis Stack components at a glance.
Uses DataTable for consistent styling with other tables in the app.
"""

import flet as ft

from app.components.frontend.controls import DataTable, DataTableColumn
from app.services.documents.health import DOCUMENTS_MODAL_ID
from app.services.finance.constants import FINANCE_COMPONENT_NAME
from app.services.system.models import ComponentStatus
from app.services.system.ui import get_component_subtitle, get_database_subtitle

from .cards.card_utils import (
    _open_modal,
    create_header_row,
)


def get_component_display_info(
    component_name: str, component_data: ComponentStatus
) -> tuple[str, str]:
    """
    Get display title and subtitle for a component.

    Returns:
        Tuple of (title, subtitle)
    """
    metadata = component_data.metadata or {}

    # Map component names to their display info
    if component_name == "backend":
        return ("Server", "FastAPI + Flet")

    elif component_name == "database":
        return ("Database", get_database_subtitle(metadata))

    elif component_name == "worker":
        return ("Worker", get_component_subtitle("worker", metadata))

    elif component_name == "cache":
        return ("Cache", "Redis")

    elif component_name == "ollama":
        version = metadata.get("version", "")
        subtitle = f"Ollama v{version}" if version else "Ollama"
        return ("Inference", subtitle)

    elif component_name == "scheduler":
        return ("Scheduler", "APScheduler")

    elif component_name == "service_auth":
        return ("Auth Service", "JWT Authentication")

    elif component_name == "service_ai":
        engine = metadata.get("engine", "AI Engine")
        engine_display_map = {
            "pydantic-ai": "Pydantic AI",
            "langchain": "LangChain",
        }
        subtitle = engine_display_map.get(
            engine, engine.replace("-", " ").title() if engine else "AI Engine"
        )
        return ("AI Service", subtitle)

    elif component_name == "service_comms":
        return ("Comms Service", "Resend + Twilio")

    elif component_name == DOCUMENTS_MODAL_ID:
        return ("Documents", get_component_subtitle(DOCUMENTS_MODAL_ID, metadata))

    elif component_name == f"service_{FINANCE_COMPONENT_NAME}":
        return (
            "Finance",
            get_component_subtitle(f"service_{FINANCE_COMPONENT_NAME}", metadata),
        )

    elif component_name == "frontend":
        return ("Frontend", "Flet")

    else:
        # Generic fallback, which is the path every plugin takes: the name
        # comes from the health metadata the plugin declares.
        display_name = component_name.replace("_", " ").replace("service ", "").title()
        subtitle = get_component_subtitle(component_name, metadata)
        return (display_name, "" if subtitle == display_name else subtitle)


def create_status_cell(
    component_name: str, component_data: ComponentStatus
) -> ft.Control:
    """Create a clickable status cell using create_header_row styling."""
    title, subtitle = get_component_display_info(component_name, component_data)

    # Use create_header_row for consistent card-like styling
    # Pass padding=0 for table rows (DataTableRow handles row spacing)
    content = create_header_row(
        title, subtitle, component_data, padding=ft.padding.all(0)
    )

    # Wrap in a container to handle clicks
    return ClickableStatusCell(component_name, component_data, content)


class ClickableStatusCell(ft.Container):
    """A clickable cell that opens the component's modal."""

    def __init__(
        self,
        component_name: str,
        component_data: ComponentStatus,
        content: ft.Control,
    ) -> None:
        super().__init__()
        self._component_name = component_name
        self._component_data = component_data
        self.content = content
        # No hover handler - DataTableRow handles hover effects
        self.on_click = self._handle_click

    def _handle_click(self, e: ft.ControlEvent) -> None:
        """Handle cell click by opening the component's detail modal."""
        if not e.page:
            return

        _open_modal(self._component_name, self._component_data, e.page)


class StatusOverviewPanel(ft.Container):
    """
    Status overview panel showing all components at a glance.

    Uses DataTable for consistent styling with other tables.
    """

    def __init__(self) -> None:
        """Initialize the status overview panel."""
        super().__init__()

        # Single column with "Aegis Stack" header
        self._columns = [DataTableColumn("Aegis Stack")]

        # Placeholder - will be populated by update_components
        self._table: DataTable | None = None

        # Initial empty table
        self._table = DataTable(
            columns=self._columns,
            rows=[],
            row_padding=8,
            empty_message="Loading components...",
        )
        self.content = self._table

        # What the current table was built from, so an unchanged refresh
        # can be skipped entirely.
        self._components: dict[str, ComponentStatus] = {}

    def update_components(self, components: dict[str, ComponentStatus]) -> None:
        """
        Update the panel with new component data.

        Args:
            components: Dictionary mapping component names to ComponentStatus
        """
        # Rebuilding assigns a brand new DataTable, so every row and cell
        # is a new object and Flet sends a full replacement. Measured at
        # 231 controls with 1 surviving a refresh whose rendered output
        # was identical - which in the steady state is every refresh.
        if self._components == components:
            return
        self._components = dict(components)

        # Define display order (most important first)
        display_order = [
            "backend",
            "database",
            "ollama",
            "cache",
            "worker",
            "scheduler",
            "service_ai",
            "service_comms",
            DOCUMENTS_MODAL_ID,
            f"service_{FINANCE_COMPONENT_NAME}",
        ]

        # Build rows in order
        rows = []
        added = set()
        for comp_name in display_order:
            if comp_name in components:
                rows.append([create_status_cell(comp_name, components[comp_name])])
                added.add(comp_name)

        # Add any remaining components not in the display order
        for comp_name, comp_data in components.items():
            if comp_name not in added and comp_name != "frontend":
                rows.append([create_status_cell(comp_name, comp_data)])

        # Rebuild table with new rows
        self._table = DataTable(
            columns=self._columns,
            rows=rows,
            row_padding=8,
        )
        self.content = self._table
