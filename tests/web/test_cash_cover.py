"""The cover sheet a cash account gets.

A card's Overview showed what it costs, a property's showed what it is
worth, and a checking account's showed nothing at all - an empty page
that read as broken when the data was there the whole time. What a
cash account is FOR is flow, so that is what its cover says.
"""

from datetime import timedelta

from app.components.web_frontend.routes.finance.cover import cash_terms
from app.services.finance.domains.planning.recurring.upcoming import Scheduled, totalled
from app.services.finance.schemas.accounts import AccountResponse
from app.services.finance.utils import current_date


def _account(**over: object) -> AccountResponse:
    base: dict[str, object] = {
        "id": 45,
        "name": "TOTAL CHECKING (CHASE)",
        "account_type": "checking",
        "classification": "asset",
        "current_balance": 0,
        "activity_balance": 309_328,
        "currency": "USD",
        "is_manual": False,
    }
    return AccountResponse(**(base | over))


def _coming(*amounts: int) -> object:
    when = current_date() + timedelta(days=3)
    return totalled(
        [
            Scheduled(name=f"n{n}", when=when, amount_cents=a)
            for n, a in enumerate(amounts)
        ]
    )


def _cells(account: AccountResponse, scheduled: object) -> dict[str, dict[str, str]]:
    return {cell["label"]: cell for cell in cash_terms(account, scheduled)}


class TestWhatIsComingAndWhatIsLeft:
    def test_it_says_the_net_and_what_the_balance_becomes(self) -> None:
        cells = _cells(_account(), _coming(-940_317, 9_000))

        # $9,403.17 out, $90.00 in, against $3,093.28 on the books.
        assert cells["Scheduled"]["value"] == "-$9,313.17"
        assert cells["After"]["value"] == "-$6,219.89"

    def test_the_caption_counts_what_makes_it_up(self) -> None:
        cells = _cells(_account(), _coming(-20_000, -5_000, 50_000))

        assert "3" in cells["Scheduled"]["caption"]

    def test_a_single_item_is_not_called_items(self) -> None:
        cells = _cells(_account(), _coming(-20_000))

        assert "1 " in cells["Scheduled"]["caption"]
        assert "items" not in cells["Scheduled"]["caption"]


class TestWhenItStaysQuiet:
    def test_nothing_scheduled_draws_no_strip(self) -> None:
        """A strip that only restates the balance the header already
        shows is noise, not a cover."""
        assert cash_terms(_account(), _coming()) == []

    def test_a_debt_gets_the_debt_cover_not_this_one(self) -> None:
        owed = _account(classification="liability", account_type="credit_card")

        assert cash_terms(owed, _coming(-20_000, 5_000)) == []


class TestWhatTheBankSaysItCanSpend:
    def test_available_shows_when_it_differs_from_the_books(self) -> None:
        cells = _cells(_account(available_balance=250_000), _coming(-20_000, 5_000))

        assert cells["Available"]["value"] == "$2,500.00"
        assert "3,093.28" in cells["Available"]["caption"]

    def test_available_is_silent_when_the_bank_never_said(self) -> None:
        cells = _cells(_account(), _coming(-20_000, 5_000))

        assert "Available" not in cells
