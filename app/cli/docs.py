"""Documentation links CLI command.

Displays documentation URLs for installed components and services.
"""

from pathlib import Path

from rich.panel import Panel
import typer

from app.cli import theme
from app.i18n import lazy_t, t

app = typer.Typer(name="docs", help=lazy_t("docs.help"), invoke_without_command=True)
console = theme.console()

# Base URL for Aegis Stack documentation
AEGIS_BASE = "https://docs.aegis-stack.io"


def _worker_docs() -> tuple[str, str | None, str]:
    """Worker's upstream docs link depends on the configured backend.

    Backend files are renamed to canonical names at generation time, so detect
    the backend from the installed package instead.
    """
    import importlib.util

    if importlib.util.find_spec("taskiq") is not None:
        return ("/components/worker/", "https://taskiq-python.github.io/", "TaskIQ")
    if importlib.util.find_spec("dramatiq") is not None:
        return ("/components/worker/", "https://dramatiq.io/", "Dramatiq")
    return ("/components/worker/", "https://arq-docs.helpmanual.io/", "arq")


# Documentation config: name -> (aegis_path, external_url, description)
# Components
COMPONENT_DOCS: dict[str, tuple[str, str | None, str]] = {
    "backend": ("/components/backend/", "https://fastapi.tiangolo.com/", "FastAPI"),
    "frontend": ("/components/frontend/", "https://flet.dev/", "Flet"),
    "scheduler": (
        "/components/scheduler/",
        "https://apscheduler.readthedocs.io/",
        "APScheduler",
    ),
    "worker": _worker_docs(),
    "ingress": (
        "/components/ingress/",
        "https://doc.traefik.io/traefik/",
        "Traefik",
    ),
    "observability": (
        "/components/observability/",
        "https://logfire.pydantic.dev/docs/",
        "Logfire",
    ),
    "database": (
        "/components/database/",
        "https://sqlmodel.tiangolo.com/",
        "SQLModel",
    ),
}

# Services
SERVICE_DOCS: dict[str, tuple[str, str | None, str]] = {
    "auth": ("/services/auth/", None, "Authentication"),
    "ai": ("/services/ai/", None, "AI Service"),
    "comms": ("/services/comms/", None, "Communications"),
    "insights": ("/services/insights/", None, "Insights"),
    "payment": ("/services/payment/", None, "Payments"),
    "blog": ("/services/blog/", None, "Blog"),
}


def _get_app_path() -> Path:
    """Get the app directory path."""
    return Path(__file__).parent.parent


def _detect_installed() -> tuple[list[str], list[str]]:
    """Detect installed components and services by checking directories.

    Returns:
        Tuple of (components, services) lists.
    """
    app_path = _get_app_path()

    # Check components
    components = ["backend", "frontend"]  # Always present
    components_dir = app_path / "components"

    if (components_dir / "scheduler").exists():
        components.append("scheduler")
    if (components_dir / "worker").exists():
        components.append("worker")

    # ingress / observability have no package of their own; detect them via
    # their dashboard cards.
    cards_dir = components_dir / "frontend" / "dashboard" / "cards"
    if (cards_dir / "ingress_card.py").exists():
        components.append("ingress")
    if (cards_dir / "observability_card.py").exists():
        components.append("observability")

    # Check if database is present (models directory with actual models)
    models_dir = app_path / "models"
    if models_dir.exists():
        # Check for actual model files (not just __init__.py)
        model_files = [f for f in models_dir.glob("*.py") if f.name != "__init__.py"]
        if model_files:
            components.append("database")

    # Check services (any service directory we have documentation for)
    services: list[str] = []
    services_dir = app_path / "services"
    for name in SERVICE_DOCS:
        if (services_dir / name).exists():
            services.append(name)

    return components, services


def _format_docs_section(
    title: str,
    items: list[str],
    docs_config: dict[str, tuple[str, str | None, str]],
) -> list[str]:
    """Format a documentation section.

    Args:
        title: Section title (e.g., "Components")
        items: List of item names to display
        docs_config: Documentation config dict

    Returns:
        List of formatted lines.
    """
    lines: list[str] = []
    lines.append(f"[bold]{title}:[/bold]")

    for item in items:
        if item not in docs_config:
            continue

        aegis_path, external_url, description = docs_config[item]
        lines.append(f"  [{theme.ACCENT}]{item}[/{theme.ACCENT}] ({description})")
        lines.append(f"    {t('docs.guide_label')} {AEGIS_BASE}{aegis_path}")
        if external_url:
            lines.append(f"    {t('docs.docs_label')}  {external_url}")
        lines.append("")

    return lines


@app.callback(help=lazy_t("docs.help_show"))
def show() -> None:
    components, services = _detect_installed()

    lines: list[str] = []

    # Components section
    if components:
        lines.extend(
            _format_docs_section(t("docs.components"), components, COMPONENT_DOCS)
        )

    # Services section
    if services:
        lines.extend(_format_docs_section(t("docs.services"), services, SERVICE_DOCS))

    if not lines:
        console.print(f"[{theme.WARNING}]{t('docs.no_detected')}[/{theme.WARNING}]")
        return

    # Get project name from directory
    project_name = _get_app_path().parent.name

    panel = Panel(
        "\n".join(lines).rstrip(),
        title=f"[bold]{project_name} {t('docs.documentation')}[/bold]",
        border_style="dim",
    )
    console.print(panel)


if __name__ == "__main__":
    app()
