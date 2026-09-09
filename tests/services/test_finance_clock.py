"""The finance service reads one calendar clock.

``date.today()`` is the host's local date; models, imports, the demo seed
and the scheduler are on UTC. Mixing them made the insight rules flag a
bill as missed that the seed had dated as due today, for the hours each
evening when the two dates differ. ``current_date`` is the only source.
"""

from datetime import UTC, datetime
from pathlib import Path

from app.components import web_frontend
from app.components.backend.api import finance as api_finance
from app.services import finance
from app.services.finance.utils import current_date


def test_current_date_is_the_utc_date() -> None:
    assert current_date() == datetime.now(UTC).date()


ROOTS = (
    Path(finance.__file__).parent,
    Path(api_finance.__file__).parent,
    Path(web_frontend.__file__).parent,
)

# Tests too: a test dated on the local clock passes all day and fails for
# the hours each evening when local and UTC disagree (three did, 2026-09-08).
TESTS = Path(__file__).resolve().parents[1]


def test_no_local_clock_in_finance_code() -> None:
    """The service, its API, and the web routes that render it."""
    offenders = [
        str(p)
        for root in (*ROOTS, TESTS)
        for p in root.rglob("*.py")
        if p != Path(__file__)
        and (
            "date.today()" in p.read_text() or "datetime.now().date()" in p.read_text()
        )
    ]
    assert offenders == [], f"local-clock reads (use utils.current_date): {offenders}"
