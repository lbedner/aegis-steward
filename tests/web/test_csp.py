"""The script Content-Security-Policy as the pages meet it (#492).

The header and its nonce come from the security-headers middleware; the
nonce reaches a template as the ``csp_nonce()`` global, so a page, a
partial and a macro all print the one the header names. These walk the
sections as a browser gets them and hold the templates to the rule the
policy enforces: no inline script without the nonce, no ``on*=`` handler.
"""

from collections.abc import Callable
from pathlib import Path
import re

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from httpx import Response
import pytest
from starlette.responses import Response as StarletteResponse

from app.components.backend.middleware.security_headers import (
    SecurityHeadersMiddleware,
)
from app.components.web_frontend.assets import COMPONENT_DIR
from app.components.web_frontend.nav import NAV
from app.components.web_frontend.rendering import render
from tests.web.dom import one, select
from tests.web.overseer import sign_in, status_with

TEMPLATES = COMPONENT_DIR / "templates"
# A data block (a chart's JSON) is not script; CSP leaves it alone.
DATA = 'type="application/json"'
NONCED = 'nonce="{{ csp_nonce() }}"'


def nonce_of(response: Response) -> str:
    """The nonce the response's policy allows, which must be there."""
    found = re.search(r"'nonce-([^']+)'", response.headers["content-security-policy"])
    assert found, response.headers["content-security-policy"]
    return found.group(1)


def assert_scripts_carry(response: Response) -> None:
    nonce = nonce_of(response)
    inline = [
        script
        for script in select(response.text, "script:not([src])")
        if script.get("type") != "application/json"
    ]
    assert [s.get("nonce") for s in inline] == [nonce] * len(inline)


@pytest.mark.parametrize("asker", ["client", "hx"])
@pytest.mark.parametrize("path", [s.path for s in NAV], ids=[s.key for s in NAV])
def test_every_section_carries_the_policy_both_ways(
    request: pytest.FixtureRequest, asker: str, path: str
) -> None:
    response = request.getfixturevalue(asker).get(path)
    assert response.status_code == 200
    assert_scripts_carry(response)


def test_overseer_carries_the_policy(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    sign_in(app, monkeypatch, status_with())
    response = TestClient(app).get("/overseer")
    assert response.status_code == 200
    assert_scripts_carry(response)


class TestAPageOfItsOwn:
    """Any template rendered through ``render`` gets the request's nonce:
    the mechanism a new page (a sign-in, say) leans on."""

    @pytest.fixture
    def app(self, add_template: Callable[[str, str], None]) -> FastAPI:
        add_template(
            "pages/_csp_probe.html",
            "{% extends layout %}{% block app_content %}"
            f'<script id="probe" {NONCED}>1</script>'
            "{% endblock %}",
        )
        tiny = FastAPI()

        @tiny.get("/probe")
        def probe(request: Request) -> StarletteResponse:
            return render(request, "pages/_csp_probe.html")

        tiny.add_middleware(SecurityHeadersMiddleware)
        return tiny

    def test_its_inline_script_carries_the_header_nonce(
        self, client: TestClient
    ) -> None:
        response = client.get("/probe")
        assert one(response.text, "script#probe").get("nonce") == nonce_of(response)


def _templates() -> list[tuple[Path, str]]:
    return [(path, path.read_text()) for path in sorted(TEMPLATES.rglob("*.html"))]


def test_no_template_has_an_inline_script_without_the_nonce() -> None:
    bare = [
        f"{path.relative_to(TEMPLATES)}: <script{attrs}>"
        for path, source in _templates()
        for attrs in re.findall(r"<script\b([^>]*)>", source)
        if "src=" not in attrs and DATA not in attrs and NONCED not in attrs
    ]
    assert not bare, "the policy blocks these:\n" + "\n".join(bare)


def test_base_loads_no_script_from_another_origin() -> None:
    """Third-party scripts are vendored (static/vendor): the policy allows
    no CDN, and any off-origin src would be a host it has to trust."""
    source = (TEMPLATES / "base.html").read_text()
    offsite = re.findall(r"""<script\b[^>]*\bsrc=["'](?:https?:)?//[^"']*""", source)
    assert not offsite, offsite


def test_no_template_has_an_inline_event_handler() -> None:
    """``onclick=`` and kin are inline script no nonce can bless: use
    Alpine (``@click``) or a listener in static JS."""
    handlers = [
        f"{path.relative_to(TEMPLATES)}: {match}"
        for path, source in _templates()
        for match in re.findall(r"\son[a-z]+\s*=", source)
    ]
    assert not handlers, "\n".join(handlers)
