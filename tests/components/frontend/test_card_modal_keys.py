"""Every card opens a modal that exists, and every component has one.

Cards and modals meet on one string: the card's ``component_name`` is the
key the modal registers under. Three service cards shipped with a bare
name while their modals were keyed by the health-tree id, and the click
was a silent no-op for months. This walks every card so that class of
gap fails here instead.
"""

from pathlib import Path
import re

from app.components.frontend.dashboard import cards
from app.components.frontend.dashboard.modal_registry import modal_registry
from app.services.system.models import ComponentStatus, ComponentStatusType

# The aggregate card expands the services group in place; it has no modal.
_NO_MODAL = {"ServicesCard"}


def test_every_card_routes_to_a_registered_modal() -> None:
    registry = modal_registry()
    for name in cards.__all__:
        if name in _NO_MODAL:
            continue
        card = getattr(cards, name)(
            ComponentStatus(
                name=name, status=ComponentStatusType.HEALTHY, message="ok", metadata={}
            )
        ).build()
        assert card.component_name in registry, (
            f"{name} opens {card.component_name!r}, which no modal registers under"
        )


# Health checks are registered with a literal name, one call each, in the
# startup hook - so the file that registers them IS the list of what the
# dashboard has to be able to open.
_REGISTRAR = re.compile(r'register_(?:service_)?health_check\(\s*"([a-z_0-9]+)"')
_HEALTH_HOOK = (
    Path(__file__).resolve().parents[3]
    / "app/components/backend/startup/component_health.py"
)


def test_every_registered_component_opens_a_modal() -> None:
    """The other direction, and the one that was missing.

    ``test_every_card_routes_to_a_registered_modal`` walks cards, so a
    component with no card of its own was invisible to it -
    ``web_frontend`` reported health, appeared as a status row and a
    diagram node, and had neither a card branch nor a modal. Both
    surfaces logged a warning on every refresh and did nothing on click.

    A service registers under a bare name and is keyed with the
    ``service_`` prefix by the time it reaches a card, so either spelling
    counts.
    """
    registry = modal_registry()
    registered = sorted(set(_REGISTRAR.findall(_HEALTH_HOOK.read_text())))
    assert registered, "no health checks found - has the hook moved?"
    missing = [
        name
        for name in registered
        if name not in registry and f"service_{name}" not in registry
    ]
    assert not missing, f"registered components with no modal: {missing}"
