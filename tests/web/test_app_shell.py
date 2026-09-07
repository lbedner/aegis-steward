"""The app shell: the frame every section page renders inside.

Rendered straight from the Jinja environment with a probe child, so the
shell is pinned independently of any route (routes arrive in #8).
"""

from app.components.web_frontend.main import templates
from tests.web.dom import none, one

PROBE = '<p id="probe">hello</p>'


def render_shell(body: str = PROBE) -> str:
    template = templates.env.from_string(
        '{% extends "layouts/app_shell.html" %}'
        "{% block app_content %}" + body + "{% endblock %}"
    )
    return template.render()


class TestAppShell:
    def test_child_content_lands_in_the_swap_target(self) -> None:
        """``#app-content`` is the one element htmx swaps section fragments
        into, so the child block must render inside it and nowhere else."""
        page = render_shell()
        one(page, "main#app-content #probe")
        one(page, "main")

    def test_sidebar_is_the_navigation(self) -> None:
        page = render_shell()
        one(page, "aside#sidebar nav")
        # The base layout's top bar is replaced, not stacked on top.
        none(page, 'a[href="/dashboard"]')

    def test_mobile_toggle_controls_the_sidebar(self) -> None:
        toggle = one(render_shell(), 'button[aria-label="Toggle navigation"]')
        assert toggle.get("aria-controls") == "sidebar"
        assert toggle.get(":aria-expanded") is not None

    def test_snackbar_sits_outside_the_scroll_container(self) -> None:
        """Fixed to the viewport regardless of section scroll, so it must
        not be a descendant of ``#app-content``."""
        page = render_shell()
        one(page, '[x-data="snackbar()"]')
        none(one(page, "main#app-content"), '[x-data="snackbar()"]')

    def test_is_not_indexable(self) -> None:
        one(render_shell(), 'meta[name="robots"][content="noindex, nofollow"]')

    def test_carries_no_pulse_user_gate(self) -> None:
        """Steward has no auth; the shell must not wait on a user object
        before painting."""
        page = render_shell()
        for banned in ("appShellGate", "__APP_USER", "/api/v1/auth/me"):
            assert banned not in page, banned
        none(page, "main#app-content script")
