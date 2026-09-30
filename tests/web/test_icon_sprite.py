"""The icons, one home each (templates/components/macros/icons.html).

A page carries the sprite once and draws icons by pointing at it: a copy
button under each of hundreds of messages repeated its path hundreds of
times, and the same chevron was drawn in six templates.
"""

from __future__ import annotations

from pathlib import Path
import re

from fastapi.testclient import TestClient

from tests.web.dom import one, select

WEB = Path("app/components/web_frontend/templates")
ICONS = WEB / "components/macros/icons.html"


def _symbols() -> dict[str, str]:
    """``{name: path data}`` of every icon in the sprite."""
    return dict(
        re.findall(
            r'<symbol id="i-([\w-]+)"[^>]*>(?:<circle[^>]*/>)?<path[^>]*\sd="([^"]*)"',
            ICONS.read_text(),
        )
    )


def test_a_page_carries_the_sprite_once_and_draws_from_it(client: TestClient) -> None:
    page = client.get("/chat").text
    sprite = one(page, "svg[data-icons]")
    names = {s.get("id") for s in select(sprite, "symbol")}
    used = {u.get("href") for u in select(page, "svg use")}
    assert used, "the chat page draws its icons from the sprite"
    assert {href.removeprefix("#") for href in used} <= names


def test_no_template_draws_a_sprite_icon_itself() -> None:
    """An icon the sprite holds is drawn with ``icon(name)``, never pasted."""
    paths = set(_symbols().values())
    pasted = [
        str(path.relative_to(WEB))
        for path in WEB.rglob("*.html")
        if path != ICONS
        and any(d in paths for d in re.findall(r'\sd="([^"]*)"', path.read_text()))
    ]
    # The landing navbar is template scaffolding nothing renders.
    assert pasted in ([], ["components/landing/navbar.html"])
