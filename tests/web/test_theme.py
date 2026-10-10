"""Theming: theme x mode x finish, generated from one palette table.

Three axes. ``theme`` is voice and shape (aegis: operational, steward:
personal); ``mode`` is light or dark (or system, resolved client-side);
``finish`` is how surfaces are lit (matte, lustre).
``tailwind.config.js`` combines them into eight DaisyUI themes named
``<theme>-<mode>-<finish>``; every color a template uses is a ``aegis-*`` name that
reads DaisyUI's variables, so nothing is defined twice. These tests keep
that single rebrand point single.
"""

import json
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any

from fastapi.testclient import TestClient
import pytest

from tests.web.dom import one, select

WEB = Path("app/components/web_frontend")
INPUT_CSS = WEB / "static/input.css"
TAILWIND = Path("tailwind.config.js")
THEMES = ("aegis", "steward")
MODES = ("dark", "light")
FINISHES = ("matte", "lustre")
DEFAULT = "aegis-dark-matte"
# The finish tokens: how a surface is lit. Matte sets every one to a no-op,
# so the classes that read them never need to know which theme is on.
FINISH = (
    "--aegis-sheen",
    "--aegis-edge",
    "--aegis-elevation",
    "--aegis-lift",
    "--aegis-glow",
    "--aegis-frost",
    "--aegis-float-alpha",
)
# The floating shadow is spelled once, in ``.floating``; a template that
# writes its own is a second finish the theme cannot reach.
SHADOW_RECIPE = re.compile(r"\bshadow-(?:sm|md|lg|xl|2xl)\b")
# The raised surface is spelled once, in ``.raised``.
RAISED_RECIPE = re.compile(
    r"bg-aegis-card border border-aegis-border rounded-lg"
    r"|rounded-lg border border-aegis-border bg-aegis-card"
)

HEX = re.compile(r"#[0-9A-Fa-f]{6}\b|#[0-9A-Fa-f]{3}\b(?![\w-])")
# Classes that hard-code what a theme decides: a literal color (looks
# right on one mode, wrong on the other) or a voice (uppercase micro-labels
# are aegis; steward speaks in sentence case). Use the token or the
# ``micro-label`` / ``caps`` classes instead.
THEME_BLIND = re.compile(
    r"\b(?:[\w-]+:)?(?:text|bg|border|ring|divide)-(?:white|black|gray-\d+|red-\d+)\b"
    r"|\b(?:uppercase|tracking-wider)\b"
)
# DaisyUI control classes are spelled once, in the form macros
# (``control`` and ``button_classes``); a template that writes them out
# is a second recipe waiting to drift.
CONTROL_RECIPE = re.compile(
    r"\b(?:btn|input|select|textarea|checkbox|radio)-(?:xs|sm|bordered|primary)\b"
    # The chosen-chip fill belongs to ``.chip`` in input.css, not a template.
    r"|\bpeer-checked:|\baria-\[pressed"
)
FORM_MACROS = WEB / "templates/components/macros/form.html"
# A class name assembled from parts (``btn-{{ size }}``) never reaches
# Tailwind's scanner, so it never compiles; spell every name in full.
BUILT_FROM_PARTS = re.compile(r'class="[^"]*[\w-]-{{')


# An ``aegis-*`` color as a utility names it: text-aegis-teal, bg-aegis-card/60.
AEGIS_COLOR = re.compile(
    r"\b(?:text|bg|border|ring|divide|fill|stroke|outline|from|via|to)-aegis-([a-z]+)\b"
)


def tailwind(path: str) -> Any:
    """``path`` of the Tailwind config (``.daisyui.themes``), read with node
    alone. The config requires the DaisyUI plugin, which only exists after
    an npm install and is never called here, so that one module is stubbed."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not installed")
    script = (
        "const M = require('module'); const load = M.prototype.require;"
        " M.prototype.require = function (id) {"
        "   return id === 'daisyui' ? {} : load.apply(this, arguments); };"
        f" console.log(JSON.stringify(require('./tailwind.config.js'){path}))"
    )
    out = subprocess.run(
        [node, "-e", script], capture_output=True, text=True, check=True
    ).stdout
    return json.loads(out)


@pytest.fixture(scope="module")
def daisy_themes() -> dict[str, dict[str, str]]:
    return {
        name: body
        for entry in tailwind(".daisyui.themes")
        for name, body in entry.items()
    }


class TestMatrix:
    def test_every_theme_mode_pair_ships(
        self, daisy_themes: dict[str, dict[str, str]]
    ) -> None:
        assert set(daisy_themes) == {
            f"{t}-{m}-{f}" for t in THEMES for m in MODES for f in FINISHES
        }

    def test_pairs_define_the_same_keys(
        self, daisy_themes: dict[str, dict[str, str]]
    ) -> None:
        keys = {frozenset(body) for body in daisy_themes.values()}
        assert len(keys) == 1
        assert {
            "primary",
            "base-100",
            "base-200",
            "base-300",
            "base-content",
            "neutral",
        } <= next(iter(keys))

    def test_shape_and_voice_follow_the_theme(
        self, daisy_themes: dict[str, dict[str, str]]
    ) -> None:
        for mode in MODES:
            aegis, steward = (
                daisy_themes[f"aegis-{mode}-matte"],
                daisy_themes[f"steward-{mode}-matte"],
            )
            assert aegis["--rounded-box"] != steward["--rounded-box"]
            assert aegis["--aegis-label-case"] == "uppercase"
            assert steward["--aegis-label-case"] == "none"

    def test_matte_leaves_the_finish_off(
        self, daisy_themes: dict[str, dict[str, str]]
    ) -> None:
        """Matte is how every theme has always looked: each finish token is
        the same no-op whatever the theme, so gloss is something people
        pick, never a change to what they already use."""
        for mode in MODES:
            aegis, steward = (
                daisy_themes[f"aegis-{mode}-matte"],
                daisy_themes[f"steward-{mode}-matte"],
            )
            assert {k: aegis[k] for k in FINISH} == {k: steward[k] for k in FINISH}
            assert aegis["--aegis-sheen"] == "none"
            assert aegis["--aegis-frost"] == "none"
            assert aegis["--aegis-float-alpha"] == "1"

    def test_lustre_lights_the_finish_and_nothing_else(
        self, daisy_themes: dict[str, dict[str, str]]
    ) -> None:
        """Lustre on any theme is that theme, lit: every finish token
        changes, and its palette, shape and voice do not."""
        for theme in THEMES:
            for mode in MODES:
                matte = daisy_themes[f"{theme}-{mode}-matte"]
                lustre = daisy_themes[f"{theme}-{mode}-lustre"]
                assert all(lustre[k] != matte[k] for k in FINISH), (theme, mode)
                rest = set(matte) - set(FINISH)
                assert {k: lustre[k] for k in rest} == {k: matte[k] for k in rest}
        # a white edge vanishes on a white card: light gets its own values
        assert (
            daisy_themes["aegis-light-lustre"]["--aegis-edge"]
            != (daisy_themes["aegis-dark-lustre"]["--aegis-edge"])
        )

    def test_surfaces_read_the_finish(self) -> None:
        css = INPUT_CSS.read_text()
        for cls in (".raised", ".floating"):
            assert cls in css
        for token in FINISH:
            assert f"var({token})" in css, token

    def test_chart_ramp_is_a_token_set(
        self, daisy_themes: dict[str, dict[str, str]]
    ) -> None:
        for body in daisy_themes.values():
            assert len([k for k in body if k.startswith("--aegis-chart-")]) >= 8

    def test_tailwind_colors_read_daisyui_variables(self) -> None:
        """``bg-aegis-card`` and friends keep working; the values come from
        the active DaisyUI theme rather than a second table."""
        config = TAILWIND.read_text()
        for name in ("bg", "card", "border", "text", "muted", "teal", "amber", "error"):
            assert re.search(rf'{name}: daisy\("[\w-]+"\)', config), name
        assert "[data-theme" not in INPUT_CSS.read_text()

    def test_every_aegis_color_in_use_is_defined(self) -> None:
        """An undefined one renders nothing in a template and fails the CSS
        build in an ``@apply``: ``aegis-accent`` did both, unnoticed until a
        page used the scheduler clock."""
        sources = [INPUT_CSS, *WEB.joinpath("templates").rglob("*.html")]
        used = {
            name for path in sources for name in AEGIS_COLOR.findall(path.read_text())
        }
        assert used - set(tailwind(".theme.extend.colors.aegis")) == set()

    def test_radius_and_voice_come_from_tokens(self) -> None:
        config = TAILWIND.read_text()
        assert 'DEFAULT: "var(--rounded-btn)"' in config
        assert 'lg: "var(--rounded-box)"' in config
        css = INPUT_CSS.read_text()
        assert ".micro-label" in css and "var(--aegis-label-case)" in css
        assert "font-size: var(--aegis-scale)" in css


class TestNoLiterals:
    @pytest.mark.parametrize(
        "path",
        sorted(p for p in WEB.rglob("*.html"))
        + sorted((WEB / "static/js").glob("*.js")),
        ids=lambda p: str(p.relative_to(WEB)),
    )
    def test_no_hex_outside_the_token_definitions(self, path: Path) -> None:
        source = path.read_text()
        # id selectors (#app-content) share the sigil; strip them first.
        source = re.sub(r"#[a-z][\w-]*[g-z_-][\w-]*", "", source)
        assert not HEX.search(source), HEX.search(source)

    @pytest.mark.parametrize(
        "path",
        sorted(WEB.rglob("*.html")),
        ids=lambda p: str(p.relative_to(WEB)),
    )
    def test_no_theme_blind_classes(self, path: Path) -> None:
        found = THEME_BLIND.findall(path.read_text())
        assert not found, found


class TestOneRecipe:
    @pytest.mark.parametrize(
        "path",
        sorted(WEB.rglob("*.html")),
        ids=lambda p: str(p.relative_to(WEB)),
    )
    def test_surfaces_come_from_the_surface_classes(self, path: Path) -> None:
        source = path.read_text()
        found = SHADOW_RECIPE.findall(source) + RAISED_RECIPE.findall(source)
        assert not found, found

    @pytest.mark.parametrize(
        "path",
        sorted(p for p in WEB.rglob("*.html") if p != FORM_MACROS),
        ids=lambda p: str(p.relative_to(WEB)),
    )
    def test_controls_come_from_the_form_macros(self, path: Path) -> None:
        found = CONTROL_RECIPE.findall(path.read_text())
        assert not found, found

    @pytest.mark.parametrize(
        "path",
        sorted(WEB.rglob("*.html")),
        ids=lambda p: str(p.relative_to(WEB)),
    )
    def test_no_class_name_is_built_from_parts(self, path: Path) -> None:
        found = BUILT_FROM_PARTS.findall(path.read_text())
        assert not found, found


class TestSwitching:
    def test_theme_script_runs_before_first_paint(self, client: TestClient) -> None:
        """A sync script in <head>, ahead of the stylesheet, applies the
        stored theme so a light-mode user never sees a dark flash."""
        page = client.get("/overview").text
        head = one(page, "head")
        srcs = [
            (el.tag, el.get("src") or el.get("href") or "")
            for el in select(head, "script[src], link[rel=stylesheet]")
        ]
        theme = next(i for i, (_, s) in enumerate(srcs) if "js/theme" in s)
        stylesheet = next(i for i, (tag, _) in enumerate(srcs) if tag == "link")
        assert theme < stylesheet
        assert one(head, 'script[src*="js/theme"]').get("defer") is None

    def test_html_carries_the_default_theme(self, client: TestClient) -> None:
        assert one(client.get("/overview").text, "html").get("data-theme") == DEFAULT

    def test_choices_are_listed_once(self, client: TestClient) -> None:
        """The server owns the list: the sidebar renders it and <html>
        carries it for theme.js, so a new theme is one line in Python
        rather than one in each of three files."""
        html = one(client.get("/overview").text, "html")
        choices = json.loads(html.get("data-appearance-choices") or "{}")
        assert choices["theme"] == list(THEMES)
        assert choices["mode"] == [*MODES, "system"]
        assert choices["finish"] == list(FINISHES)
        script = (WEB / "static/js/theme.js").read_text()
        assert "'steward'" not in script and "'lustre'" not in script

    def test_sidebar_offers_every_theme_and_mode(self, client: TestClient) -> None:
        aside = one(client.get("/overview").text, "aside#sidebar")
        assert one(aside, "button[data-appearance]").get("aria-label")
        assert {
            b.get("data-set-theme") for b in select(aside, "button[data-set-theme]")
        } == set(THEMES)
        assert {
            b.get("data-set-mode") for b in select(aside, "button[data-set-mode]")
        } == set(MODES) | {"system"}
        assert {
            b.get("data-set-finish") for b in select(aside, "button[data-set-finish]")
        } == set(FINISHES)


class TestHidden:
    def test_hidden_beats_a_display_utility(self) -> None:
        """``hidden`` is how every "nothing here yet" element hides, so it
        has to win against the ``block``/``flex`` class beside it. Without
        this the empty pending-changes banner reads "0 changes awaiting
        your review"."""
        css = INPUT_CSS.read_text()
        assert "[hidden]" in css
        rule = css[css.index("[hidden]") :]
        assert "display: none !important" in rule.split("}")[0]
