"""Pattern 6, feedback: toasts, empty states, error banners.

Toasts are driven by the ``HX-Trigger`` response header. A route says
``with_toast(response, "Saved")``; htmx raises a ``toast`` DOM event; the
region in the base layout shows it. No reload, no storage.
"""

import json

from fastapi.testclient import TestClient
import pytest
from starlette.responses import Response

from app.components.web_frontend.rendering import navigate, templates, with_toast
from tests.web.dom import none, one, select, text


class TestWithToast:
    def test_sets_the_trigger_header(self) -> None:
        response = with_toast(Response(), "Saved")
        assert json.loads(response.headers["HX-Trigger"]) == {
            "toast": {"text": "Saved", "tone": "ok"}
        }

    def test_error_tone(self) -> None:
        response = with_toast(Response(), "Nope", tone="error")
        assert json.loads(response.headers["HX-Trigger"])["toast"]["tone"] == "error"

    def test_merges_with_an_existing_trigger(self) -> None:
        """A route may already be triggering another event; both must
        survive in the one header htmx reads."""
        response = Response()
        response.headers["HX-Trigger"] = json.dumps({"count-changed": {"n": 3}})
        with_toast(response, "Saved")
        triggers = json.loads(response.headers["HX-Trigger"])
        assert triggers["count-changed"] == {"n": 3}
        assert triggers["toast"]["text"] == "Saved"


class TestToastRegion:
    @pytest.fixture
    def page(self, client: TestClient) -> str:
        return client.get("/overview").text

    def test_present_outside_the_content_area(self, page: str) -> None:
        """Lives in the base layout, not the swap target, so it survives
        section navigation and stays fixed to the viewport."""
        one(page, "#toasts")
        none(one(page, "main#app-content"), "#toasts")

    def test_is_a_polite_live_region(self, page: str) -> None:
        assert one(page, "#toasts").get("aria-live") == "polite"

    def test_listens_for_the_toast_event(self, page: str) -> None:
        assert one(page, "#toasts").get("@toast.window") is not None

    def test_reload_snackbar_is_gone(self, page: str) -> None:
        for banned in ("appFlashSnackbar", "__app_snackbar", 'x-data="snackbar()"'):
            assert banned not in page, banned


def render(source: str, **context: object) -> str:
    return templates.env.from_string(
        '{% from "components/macros/feedback.html" import empty_state, error_banner %}'
        + source
    ).render(**context)


class TestEmptyState:
    def test_names_what_is_missing_and_what_to_do(self) -> None:
        html = render('{{ empty_state("No accounts yet", "Add one to begin.") }}')
        assert text(one(html, "h2")) == "No accounts yet"
        assert "Add one to begin." in text(one(html, "div"))

    def test_hint_is_optional(self) -> None:
        html = render('{{ empty_state("Nothing here") }}')
        assert text(one(html, "h2")) == "Nothing here"
        none(html, "p")


class TestErrorBanner:
    def test_renders_nothing_without_errors(self) -> None:
        assert render("{{ error_banner([]) }}").strip() == ""
        assert render("{{ error_banner(none) }}").strip() == ""

    def test_lists_every_error_as_an_alert(self) -> None:
        html = render(
            '{{ error_banner(["Amount is required", "Date is in the future"]) }}'
        )
        banner = one(html, '[role="alert"]')
        assert [text(li) for li in select(banner, "li")] == [
            "Amount is required",
            "Date is in the future",
        ]


class TestNavigate:
    def test_closes_the_dialog_before_htmx_follows_the_location(self) -> None:
        """HX-Location is followed instead of swapped, so an after-settle
        close would never fire; the plain trigger is processed first."""
        response = navigate(Response(), "/accounts/3")
        assert json.loads(response.headers["HX-Location"])["path"] == "/accounts/3"
        assert json.loads(response.headers["HX-Trigger"]) == {"dialog:close": None}


class TestAction:
    def test_one_recipe_with_tones(self) -> None:
        source = '{% from "components/macros/form.html" import action %}'
        quiet = one(render(source + '{{ action("Edit", \'hx-get="/e"\') }}'), "button")
        danger = one(render(source + '{{ action("Remove", \'hx-delete="/r"\', tone="danger") }}'), "button")
        assert quiet.get("hx-get") == "/e" and quiet.get("type") == "button"
        assert "border-aegis-border" in quiet.get("class")
        assert "text-error" in danger.get("class") and danger.get("hx-delete") == "/r"

