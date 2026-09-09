"""Theming: theme x mode, generated from one palette table.

Two axes. ``theme`` is voice and shape (aegis: operational, steward:
personal); ``mode`` is light or dark (or system, resolved client-side).
``tailwind.config.js`` combines them into four DaisyUI themes named
``<theme>-<mode>``; every color a template uses is a ``aegis-*`` name that
reads DaisyUI's variables, so nothing is defined twice. These tests keep
that single rebrand point single.
"""

import json
from pathlib import Path
import re
import shutil
import subprocess

from fastapi.testclient import TestClient
import pytest

from tests.web.dom import one, select

WEB = Path("app/components/web_frontend")
INPUT_CSS = WEB / "static/input.css"
TAILWIND = Path("tailwind.config.js")
THEMES = ("aegis", "steward")
MODES = ("dark", "light")
DEFAULT = "aegis-dark"

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


@pytest.fixture(scope="module")
def daisy_themes() -> dict[str, dict[str, str]]:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not installed")
    # The config requires the DaisyUI plugin, which only exists after an
    # npm install; the theme table does not need it, so stub that one
    # module and read the table with node alone.
    script = (
        "const M = require('module'); const load = M.prototype.require;"
        " M.prototype.require = function (id) {"
        "   return id === 'daisyui' ? {} : load.apply(this, arguments); };"
        " console.log(JSON.stringify(require('./tailwind.config.js').daisyui.themes))"
    )
    out = subprocess.run(
        [node, "-e", script], capture_output=True, text=True, check=True
    ).stdout
    return {name: body for entry in json.loads(out) for name, body in entry.items()}


class TestMatrix:
    def test_every_theme_mode_pair_ships(
        self, daisy_themes: dict[str, dict[str, str]]
    ) -> None:
        assert set(daisy_themes) == {f"{t}-{m}" for t in THEMES for m in MODES}

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
                daisy_themes[f"aegis-{mode}"],
                daisy_themes[f"steward-{mode}"],
            )
            assert aegis["--rounded-box"] != steward["--rounded-box"]
            assert aegis["--aegis-label-case"] == "uppercase"
            assert steward["--aegis-label-case"] == "none"

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

    def test_sidebar_offers_every_theme_and_mode(self, client: TestClient) -> None:
        aside = one(client.get("/overview").text, "aside#sidebar")
        assert one(aside, "button[data-appearance]").get("aria-label")
        assert {
            b.get("data-set-theme") for b in select(aside, "button[data-set-theme]")
        } == set(THEMES)
        assert {
            b.get("data-set-mode") for b in select(aside, "button[data-set-mode]")
        } == set(MODES) | {"system"}


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
