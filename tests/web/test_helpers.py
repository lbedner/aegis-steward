"""The web test kit itself: the ``hx`` client and the DOM helpers.

Every page test in this package leans on these, so they get their own
tests. A helper that silently matched nothing would make every downstream
assertion pass vacuously.
"""

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
import pytest

from tests.web.dom import is_fragment, one, select, text

PAGE = """<!DOCTYPE html><html><head><title>t</title></head><body>
<main id="app-content">
  <table class="tbl"><tr><td class="c">a</td><td class="c">b</td></tr></table>
  <p class="msg">  hello <b>world</b> </p>
</main></body></html>"""

FRAGMENT = '<div id="row-1"><span class="amt">12.00</span></div>'


class TestSelect:
    def test_returns_every_match(self) -> None:
        assert len(select(PAGE, "td.c")) == 2

    def test_returns_empty_list_on_no_match(self) -> None:
        assert select(PAGE, "nav") == []

    def test_works_on_a_bare_fragment(self) -> None:
        """Fragments have no <html>; the parser must not choke or wrap
        them in a way that breaks selectors."""
        assert len(select(FRAGMENT, "#row-1 .amt")) == 1


class TestOne:
    def test_returns_the_single_match(self) -> None:
        assert one(PAGE, "table").get("class") == "tbl"

    def test_fails_loudly_on_zero_matches(self) -> None:
        with pytest.raises(AssertionError, match="expected 1 .* got 0"):
            one(PAGE, "nav")

    def test_fails_loudly_on_many_matches(self) -> None:
        with pytest.raises(AssertionError, match="expected 1 .* got 2"):
            one(PAGE, "td.c")


class TestText:
    def test_collapses_whitespace_and_descends(self) -> None:
        assert text(one(PAGE, "p.msg")) == "hello world"


class TestIsFragment:
    def test_full_page_is_not_a_fragment(self) -> None:
        assert not is_fragment(PAGE)

    def test_bare_markup_is_a_fragment(self) -> None:
        assert is_fragment(FRAGMENT)


@pytest.fixture
def app() -> FastAPI:
    """Override the real app with one route that echoes the htmx header."""
    tiny = FastAPI()

    @tiny.get("/echo")
    def echo(request: Request) -> dict[str, str | None]:
        return {"hx": request.headers.get("HX-Request")}

    return tiny


class TestHxClient:
    def test_hx_client_sends_the_htmx_request_header(self, hx: TestClient) -> None:
        assert hx.get("/echo").json() == {"hx": "true"}

    def test_plain_client_does_not(self, client: TestClient) -> None:
        assert client.get("/echo").json() == {"hx": None}
