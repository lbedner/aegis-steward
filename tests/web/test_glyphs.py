"""Category glyphs: every seeded category has one, keyed on our own names."""

from app.components.web_frontend.glyphs import _BY_CATEGORY, category_glyph


def test_every_mapped_category_resolves_to_path_data() -> None:
    for category in _BY_CATEGORY:
        assert (category_glyph(category) or "").startswith(("M", "m")), category


def test_a_child_category_takes_its_parents_glyph() -> None:
    assert category_glyph("Rent And Utilities:Mortgage") == category_glyph(
        "Rent And Utilities"
    )


def test_unknown_or_missing_category_has_no_glyph() -> None:
    assert category_glyph("Mystery") is None
    assert category_glyph(None) is None
