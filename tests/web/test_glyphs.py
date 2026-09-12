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


class TestAnAccountsOwnGlyph:
    """An account with no institution should still read as what it IS.

    A house for property, a card for credit, a bank for checking: the
    same three-tier mark every finance row uses, so the portfolio is
    scannable before anybody names a single bank.
    """

    def test_each_kind_has_one(self) -> None:
        from app.components.web_frontend.glyphs import account_glyph

        for kind in (
            "checking",
            "savings",
            "cash",
            "credit_card",
            "loan",
            "brokerage",
            "investment",
            "crypto",
            "property",
            "vehicle",
            "other_asset",
            "other_liability",
        ):
            assert account_glyph(kind), f"{kind} has no glyph"

    def test_property_and_credit_read_as_themselves(self) -> None:
        from app.components.web_frontend.glyphs import account_glyph, category_glyph

        assert account_glyph("property") == category_glyph("Rent And Utilities")
        assert account_glyph("credit_card") == category_glyph("Loan Payments")

    def test_something_unheard_of_has_none(self) -> None:
        from app.components.web_frontend.glyphs import account_glyph

        assert account_glyph("envelope") is None
        assert account_glyph(None) is None
