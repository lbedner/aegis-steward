"""Building one component card, and reading a status payload.

These sat inside ``setup_dashboard`` but captured nothing from it:
``create_component_card`` had no free variables at all, and the
insights override needed only the client and the page, which are
parameters now.
"""

import time
from typing import Any

import flet as ft

from app.core.log import logger
from app.services.documents.health import DOCUMENTS_MODAL_ID
from app.services.finance.constants import FINANCE_COMPONENT_NAME
from app.services.system.models import ComponentStatus, ComponentStatusType

from ..dashboard.cards import (
    AICard,
    CommsCard,
    DatabaseCard,
    DocumentsCard,
    FinanceCard,
    OllamaCard,
    RedisCard,
    SchedulerCard,
    ServerCard,
    ServicesCard,
    WorkerCard,
)

# Constants for health system grouping
COMPONENTS_GROUP_KEY = "components"
SERVICES_GROUP_KEY = "services"
SERVICE_PREFIX = "service_"


def _format_uptime(start: float) -> str:
    """Format elapsed time since start as a compact uptime string."""
    elapsed = int(time.monotonic() - start)
    minutes = elapsed // 60
    hours = minutes // 60
    days = hours // 24
    if days > 0:
        return f"Up {days}d {hours % 24}h"
    elif hours > 0:
        return f"Up {hours}h {minutes % 60}m"
    elif minutes > 0:
        return f"Up {minutes}m"
    else:
        return "Up <1m"


def _convert_component(comp_data: dict[str, Any]) -> ComponentStatus:
    """Recursively convert API component data to ComponentStatus."""
    try:
        sub_components = {}
        if "sub_components" in comp_data:
            for sub_name, sub_data in comp_data["sub_components"].items():
                sub_components[sub_name] = _convert_component(sub_data)

        # Parse status from API response
        status_str = comp_data.get("status", "unhealthy")
        try:
            status = ComponentStatusType(status_str)
        except ValueError:
            logger.warning(f"Unknown status: {status_str}, default UNHEALTHY")
            status = ComponentStatusType.UNHEALTHY

        return ComponentStatus(
            name=comp_data.get("name", "Unknown"),
            status=status,
            message=comp_data.get("message", "No message"),
            response_time_ms=comp_data.get("response_time_ms"),
            metadata=comp_data.get("metadata", {}),
            sub_components=sub_components,
        )
    except Exception as e:
        logger.error(
            f"Failed to convert component data: {e}",
            exc_info=True,
            extra={
                "error_type": type(e).__name__,
                "function": "_convert_component",
                "comp_data": comp_data,
            },
        )
        # Return a fallback ComponentStatus
        return ComponentStatus(
            name=comp_data.get("name", "Unknown"),
            status=ComponentStatusType.UNHEALTHY,
            message=f"Error converting component: {e}",
            response_time_ms=None,
            metadata={},
            sub_components={},
        )


def create_component_card(component_name: str, component_data: Any) -> ft.Container:
    """Create stunning marketing-grade component cards."""
    try:
        if not component_data:
            logger.warning(f"No component data provided for {component_name}")
            return ft.Container()

        # Map component names to their stunning card classes
        if component_name == "backend":
            return ServerCard(component_data).build()
        elif component_name == "frontend":
            # Frontend is merged into ServerCard, return empty
            return ft.Container()

        elif component_name == "worker":
            return WorkerCard(component_data).build()

        elif component_name == "cache":
            return RedisCard(component_data).build()

        elif component_name == "database":
            return DatabaseCard(component_data).build()

        elif component_name == "ollama":
            return OllamaCard(component_data).build()

        elif component_name == "scheduler":
            return SchedulerCard(component_data).build()

        elif component_name == "services":
            return ServicesCard(component_data).build()

        # Service cards - specific checks BEFORE generic fallback

        elif component_name == "service_ai":
            return AICard(component_data).build()

        elif component_name == "service_comms":
            return CommsCard(component_data).build()

        elif component_name == DOCUMENTS_MODAL_ID:
            return DocumentsCard(component_data).build()

        elif component_name == f"{SERVICE_PREFIX}{FINANCE_COMPONENT_NAME}":
            return FinanceCard(component_data).build()

        elif component_name.startswith("service_"):
            # For other services, use generic ServicesCard for now
            return ServicesCard(component_data).build()

        else:
            # Fallback for unknown components - should not happen in practice
            logger.warning(f"Unknown component type: {component_name}")
            return ft.Container(
                content=ft.Text(f"Unknown component: {component_name}"),
                padding=20,
                bgcolor=ft.Colors.SURFACE,
                border=ft.border.all(1, ft.Colors.OUTLINE_VARIANT),
                border_radius=16,
                width=800,
                height=240,
            )
    except Exception as e:
        logger.error(
            f"Failed to create component card for {component_name}: {e}",
            exc_info=True,
            extra={
                "error_type": type(e).__name__,
                "function": "create_component_card",
                "component_name": component_name,
                "component_data": component_data,
            },
        )
        # Return fallback card on error
        return ft.Container(
            content=ft.Text(f"Error loading {component_name}"),
            padding=20,
            bgcolor=ft.Colors.ERROR_CONTAINER,
            border=ft.border.all(1, ft.Colors.ERROR),
            border_radius=16,
            width=800,
            height=240,
        )
