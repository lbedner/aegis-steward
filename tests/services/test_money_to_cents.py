"""Two ways into cents, one rounding rule.

``money_to_cents`` parsed what a person typed with ``float``, and
``to_cents`` parsed a number with ``Decimal``, so they disagreed at the
half-cent: ``2.675`` is ``2.67499999...`` in binary, so float said 267
and Decimal said 268. The less correct one was on the user-entered path
- every typed amount, loan terms, figures read off documents (#215).

Both now parse with Decimal and round half UP, which is what a person
expects of money (``2.665`` is 267, not banker's 266). Their contracts
still differ on purpose: typed text answers ``None`` for garbage so a
form can say 422; a number raises.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.services.finance.utils import money_to_cents, to_cents


class TestTheHalfCent:
    @pytest.mark.parametrize(
        ("typed", "cents"),
        [("2.675", 268), ("2.665", 267), ("0.005", 1), ("-2.675", -268)],
    )
    def test_typed_money_rounds_half_up(self, typed: str, cents: int) -> None:
        assert money_to_cents(typed) == cents

    @pytest.mark.parametrize("amount", ["2.675", "2.665", "0.005", "-2.675", "1234.5"])
    def test_the_two_parsers_agree(self, amount: str) -> None:
        assert money_to_cents(amount) == to_cents(amount) == to_cents(Decimal(amount))


class TestWhatAPersonTypes:
    @pytest.mark.parametrize(
        ("typed", "cents"),
        [("$1,200.50", 120050), ("3,000", 300000), (" 12 ", 1200), ("", 0), (None, 0)],
    )
    def test_symbols_separators_and_blanks(self, typed: str | None, cents: int) -> None:
        assert money_to_cents(typed) == cents

    @pytest.mark.parametrize("typed", ["abc", "12.3.4", "inf", "nan", "-inf"])
    def test_garbage_is_none_so_the_form_can_say_so(self, typed: str) -> None:
        """Never a crash: ``float("inf") * 100`` made ``round`` raise
        OverflowError, which a form turned into a 500."""
        assert money_to_cents(typed) is None
