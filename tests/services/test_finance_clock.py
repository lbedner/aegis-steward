"""The finance service reads one calendar clock.

``date.today()`` is the host's local date; models, imports, the demo seed
and the scheduler are on UTC. Mixing them made the insight rules flag a
bill as missed that the seed had dated as due today, for the hours each
evening when the two dates differ. ``current_date`` is the only source.
"""

from datetime import UTC, datetime
from pathlib import Path

from app.services import finance
from app.services.finance.utils import current_date


def test_current_date_is_the_utc_date() -> None:
    assert current_date() == datetime.now(UTC).date()


def test_no_local_clock_in_the_finance_service() -> None:
    root = Path(finance.__file__).parent
    offenders = [
        str(p.relative_to(root))
        for p in root.rglob("*.py")
        if "date.today()" in p.read_text() or "datetime.now().date()" in p.read_text()
    ]
    assert offenders == [], f"local-clock reads (use utils.current_date): {offenders}"
