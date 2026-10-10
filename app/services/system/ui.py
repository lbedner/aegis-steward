"""
Shared UI helpers for status presentation across CLI and frontend.

Provides a single source of truth for mapping ComponentStatusType to
icons and semantic colors, so CLI and dashboard remain consistent.
"""

from app.core.constants import ComponentName

from .models import ComponentStatusType
from .topology import QUEUE


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


def registry_key(group: str, name: str) -> str:
    """An entry's key in the one name registry: a component's own name, a
    service's ``service_<name>`` (``get_component_title``)."""
    return name if group == "components" else f"service_{name}"


def get_component_title(component_name: str) -> str:
    """The one name for a component or service, on every surface.

    Components are keyed by name, services as ``service_<name>``. Anything
    unlisted (a plugin) is title-cased from its key.
    """
    mapping = {
        ComponentName.BACKEND: "Server",
        ComponentName.FRONTEND: "Flet Frontend",
        ComponentName.WEB_FRONTEND: "Web Frontend",
        ComponentName.DATABASE: "Database",
        ComponentName.CACHE: "Cache",
        ComponentName.WORKER: "Worker",
        # Not a component: the worker's queue, on Overseer's map.
        QUEUE: "Queue",
        ComponentName.SCHEDULER: "Scheduler",
        ComponentName.OLLAMA: "Inference",
        "service_ai": "AI",
        "service_comms": "Communications",
        "service_documents": "Documents",
        "service_rag": "RAG",
        # The job history the scheduler keeps, not the scheduler itself.
        "service_scheduler": "Scheduler history",
        "service_finance": "Finance",
    }
    fallback = (
        component_name.removeprefix("service_").replace("_", " ").replace("-", " ")
    )
    return mapping.get(component_name, fallback.title())


def get_component_label(component_name: str) -> str:
    """Map component keys to user-facing labels (brand or friendly name)."""
    mapping = {
        ComponentName.BACKEND: "FastAPI + Flet",
        # Both frontends ship: Flet at /dashboard, htmx pages at /.
        ComponentName.FRONTEND: "Flet + htmx",
        ComponentName.WEB_FRONTEND: "Jinja2 + htmx",
        ComponentName.DATABASE: "PostgreSQL",
        ComponentName.CACHE: "Redis",
        ComponentName.WORKER: "arq",
        ComponentName.SCHEDULER: "APScheduler",
        ComponentName.OLLAMA: "Ollama",
        "service_ai": "LLM Provider",
        "service_comms": "Resend + Twilio",
        "service_documents": "Document store",
        "service_finance": "Aggregator",
    }
    return mapping.get(component_name, component_name.replace("_", " ").title())


def get_component_subtitle(
    component_name: str, metadata: dict[str, object] | None = None
) -> str:
    """Get a versioned subtitle for a component (e.g. 'Dramatiq 1.17.0').

    The base label is whatever the health metadata declares as
    ``subtitle`` (how a plugin names itself, being in no registry),
    otherwise ``get_component_label``. The metadata ``version`` is
    appended when known. Every dashboard surface routes through here, so
    the stack view, the cards and the diagram cannot disagree.
    """
    if component_name == ComponentName.DATABASE:
        return get_database_subtitle(metadata)

    # A plugin is in no label registry, so it names itself in its health
    # metadata; that name wins over the derived one.
    metadata = metadata or {}
    declared = metadata.get("subtitle")
    label = str(declared) if declared else get_component_label(component_name)
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
