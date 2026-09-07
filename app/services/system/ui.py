"""
Shared UI helpers for status presentation across CLI and frontend.

Provides a single source of truth for mapping ComponentStatusType to
icons and semantic colors, so CLI and dashboard remain consistent.
"""

from .models import ComponentStatusType


def get_status_icon(status: ComponentStatusType) -> str:
    """Return the display glyph for a given status.

    Text glyphs (no emoji): they align to the monospace grid and render the
    same in every terminal. Color carries the state (applied by the caller);
    the glyph itself is monochrome.
    """
    if status == ComponentStatusType.HEALTHY:
        return "✓"
    if status == ComponentStatusType.INFO:
        return "ℹ"
    if status == ComponentStatusType.WARNING:
        return "⚠"
    if status == ComponentStatusType.UNHEALTHY:
        return "✗"
    return "?"


def get_status_color_name(status: ComponentStatusType) -> str:
    """Return a semantic color name for a given status (CLI friendly).

    Frontend can adapt these semantic names to theme-specific colors.
    """
    if status == ComponentStatusType.HEALTHY:
        return "green"
    if status == ComponentStatusType.INFO:
        return "blue"
    if status == ComponentStatusType.WARNING:
        return "yellow"
    if status == ComponentStatusType.UNHEALTHY:
        return "red"
    return "white"


def get_component_title(component_name: str) -> str:
    """Map component keys to category/title names for modal headers."""
    mapping = {
        "backend": "Server",
        "frontend": "Frontend",
        "web_frontend": "Web Frontend",
        "database": "Database",
        "cache": "Cache",
        "worker": "Worker",
        "scheduler": "Scheduler",
        "service_ai": "AI Service",
        "service_comms": "Communications",
        "service_finance": "Finance",
    }
    return mapping.get(component_name, component_name.replace("_", " ").title())


def get_component_label(component_name: str) -> str:
    """Map component keys to user-facing labels (brand or friendly name)."""
    mapping = {
        "backend": "FastAPI + Flet",
        # Both frontends ship: Flet at /dashboard, htmx pages at /.
        "frontend": "Flet + htmx",
        "web_frontend": "Jinja2 + htmx",
        "database": "PostgreSQL",
        "cache": "Redis",
        "worker": "arq",
        "scheduler": "APScheduler",
        "service_ai": "LLM Provider",
        "service_comms": "Resend + Twilio",
        "service_finance": "Aggregator",
    }
    return mapping.get(component_name, component_name.replace("_", " ").title())


def get_component_subtitle(
    component_name: str, metadata: dict[str, object] | None = None
) -> str:
    """Get a versioned subtitle for a component (e.g. 'Dramatiq 1.17.0').

    Uses the base label from ``get_component_label`` and appends the version
    from health-check metadata when available.
    """
    if component_name == "database":
        return get_database_subtitle(metadata)

    label = get_component_label(component_name)
    if metadata:
        version = metadata.get("version", "")
        if version and version != "unknown":
            return f"{label} {version}"
    return label


def get_database_subtitle(metadata: dict[str, object] | None = None) -> str:
    """Single source of truth for the Database subtitle (card, modal, diagram).

    Renders ``PostgreSQL <version>`` / ``SQLite <version>`` and appends
    ``(Neon)`` when connected to a Neon host (``is_neon`` in health metadata).
    """
    metadata = metadata or {}
    implementation = metadata.get("implementation", "sqlite")
    if implementation == "postgresql":
        version = metadata.get("version_short", "")
        if not version and "version" in metadata:
            full_version = metadata["version"]
            if isinstance(full_version, str) and "PostgreSQL" in full_version:
                parts = full_version.split()
                version = parts[1] if len(parts) >= 2 else ""
        subtitle = f"PostgreSQL {version}" if version else "PostgreSQL"
        if metadata.get("is_neon"):
            subtitle += " (Neon)"
    else:
        version = metadata.get("version", "")
        subtitle = f"SQLite {version}" if version else "SQLite"
    return subtitle
