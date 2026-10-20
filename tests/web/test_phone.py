"""The app on a phone: a home-screen app on iOS, and the native shells'
mode (issue 506).

Every page carries what iOS reads when it is added to the home screen:
an icon on an opaque ground, the app's name, and standalone display. A
request from the native shell (its User-Agent carries ``NATIVE_AGENT``)
renders the page without the sidebar and its top strip, because the
shell's own tab bar is the navigation there.
"""

import hashlib

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image, ImageChops
from starlette.requests import Request

from app.components.web_frontend.assets import APP_ICON, STATIC_DIR
from app.components.web_frontend.nav import NATIVE_AGENT
from app.components.web_frontend.rendering import APPEARANCE, templates
from tests.web.dom import none, one
from tests.web.test_theme import tailwind

SAFARI = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) Safari/604.1"
# favicon.svg as app-icon.png was last drawn from (``make app-icon``).
FAVICON_DRAWN = "d92ab0c9bd833884635d7af14a4f04cc70186a716676f8418975dfe067cbdb4e"


def icon_ground() -> str:
    """The ground the icon sits on: the default theme's page, dark. iOS
    shows one icon whatever the phone's mode, and the shield is drawn for
    a dark ground. ``make app-icon`` reads it from here."""
    theme, finish = (APPEARANCE[key][1][0][0] for key in ("theme", "finish"))
    name = f"{theme}-dark-{finish}"
    ground: str = tailwind(
        f".daisyui.themes.find((t) => t['{name}'])['{name}']['base-200']"
    )
    return ground


class TestHomeScreen:
    def test_every_page_names_its_home_screen_icon(self, client: TestClient) -> None:
        page = client.get("/login").text
        icon = one(page, 'link[rel="apple-touch-icon"]')
        served = client.get(icon.get("href"))
        assert served.status_code == 200
        assert served.headers["content-type"] == "image/png"

    def test_it_opens_standalone_under_the_app_name(self, client: TestClient) -> None:
        page = client.get("/login").text
        assert (
            one(page, 'meta[name="apple-mobile-web-app-capable"]').get("content")
            == "yes"
        )
        title = one(page, 'meta[name="apple-mobile-web-app-title"]').get("content")
        assert title == templates.env.globals["project_name"]

    def test_it_has_the_manifest_ios_asks_before_it_offers_notifications(
        self, client: TestClient
    ) -> None:
        """iOS lets a home-screen app ask for notifications only when its
        manifest says standalone. Fetched with the session, through the
        gate like every other path."""
        link = one(client.get("/overview").text, 'link[rel="manifest"]')
        assert link.get("crossorigin") == "use-credentials"
        manifest = client.get(link.get("href")).json()
        assert manifest["display"] == "standalone"
        assert manifest["name"] == templates.env.globals["project_name"]
        assert manifest["start_url"] == "/"
        (icon,) = manifest["icons"]
        assert client.get(icon["src"]).headers["content-type"] == "image/png"

    def test_the_icon_is_the_favicon_on_the_page_ground(self) -> None:
        """A copy of the mark and of a theme color, so it is redrawn from
        both (``make app-icon``) and a change to either fails here."""
        favicon = hashlib.sha256((STATIC_DIR / "favicon.svg").read_bytes()).hexdigest()
        assert favicon == FAVICON_DRAWN, "favicon.svg changed: make app-icon"
        with Image.open(STATIC_DIR / APP_ICON) as icon:
            corner = "#" + icon.convert("RGB").tobytes()[:3].hex().upper()
        assert corner == icon_ground().upper(), "the page ground changed: make app-icon"

    def test_the_shield_fills_the_icon_as_other_apps_do(self) -> None:
        """Beside a bank's icon, whose mark fills about four fifths of its
        tile, a shield drawn at three fifths read as shrunk."""
        with Image.open(STATIC_DIR / APP_ICON) as icon:
            drawn = icon.convert("RGB")
        ground = Image.new("RGB", drawn.size, drawn.getpixel((0, 0)))
        box = ImageChops.difference(drawn, ground).getbbox()
        assert box is not None, "the icon is all ground"
        _, top, _, bottom = box
        assert bottom - top >= 0.85 * drawn.height

    def test_the_status_bar_has_a_color_to_follow(self, client: TestClient) -> None:
        """app.js sets it from the page's own background, so the bar
        follows the theme instead of a second copy of its color."""
        one(client.get("/login").text, 'meta[name="theme-color"]')


class TestNativeMode:
    def test_the_shell_drops_the_sidebar_and_its_strip(
        self, client: TestClient
    ) -> None:
        page = client.get(
            "/overview", headers={"User-Agent": f"{SAFARI} {NATIVE_AGENT}/1"}
        ).text
        none(page, "aside#sidebar")
        none(page, 'button[aria-label="Toggle navigation"]')
        one(page, "main#app-content")

    def test_a_page_drawn_without_render_knows_it_too(self, app: FastAPI) -> None:
        """Sign-in and the Overseer answer with a bare TemplateResponse."""
        agent = f"{SAFARI} {NATIVE_AGENT}/1".encode()
        scope = {"type": "http", "app": app, "headers": [(b"user-agent", agent)]}
        request = Request(scope)
        response = templates.TemplateResponse(
            request=request, name="pages/overseer/forbidden.html"
        )
        assert response.context["native"] is True

    def test_a_browser_keeps_them(self, client: TestClient) -> None:
        page = client.get("/overview", headers={"User-Agent": SAFARI}).text
        one(page, "aside#sidebar")
        one(page, 'button[aria-label="Toggle navigation"]')
