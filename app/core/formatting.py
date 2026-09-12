"""Shared formatting utilities for display across CLI and frontend."""

from datetime import UTC, datetime


def format_number(num: int) -> str:
    """Format large numbers with commas (e.g., 1234567 -> '1,234,567')."""
    return f"{num:,}"


def format_cost(cost: float) -> str:
    """Format cost with dollar sign and appropriate decimal places.

    Uses 6 decimal places for tiny amounts (< $0.01) to show token-level
    pricing accurately, 4 decimal places otherwise for readability.
    """
    if cost < 0.01:
        return f"${cost:.6f}"
    return f"${cost:.4f}"


def format_percentage(pct: float) -> str:
    """Format percentage with one decimal place (e.g., 90.5%)."""
    return f"{pct:.1f}%"


def format_bytes(size: int | float) -> str:
    """A byte count in the unit that reads: "512 B", "9.0 MB", "3.0 GB"."""
    if size < 1024:
        return f"{int(size)} B"
    for unit in ("KB", "MB", "GB", "TB"):
        size /= 1024
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}"
    return f"{size:.1f} TB"


def format_date(value: object) -> str:
    """An ISO date (or ``date``) as "Aug 19, 2026". Blank stays blank.

    Anything unparseable is returned as-is rather than swallowed: a
    surprising string on screen is a better failure than a silently empty
    cell, and it points at the real bug instead of hiding it.
    """
    from datetime import date as _date
    from datetime import datetime as _datetime

    if value is None or value == "":
        return ""
    if isinstance(value, _datetime):
        value = value.date()
    if not isinstance(value, _date):
        try:
            value = _date.fromisoformat(str(value)[:10])
        except ValueError:
            return str(value)
    return f"{value.strftime('%b')} {value.day}, {value.year}"


def payee_label(merchant: object, merchant_name: object, name: object) -> str:
    """What a transaction is called: the payee someone named, then the one
    the source supplied, then the raw descriptor.

    One rule, because the register, the dialogs that rename a
    transaction, and the cards that propose changes to it all have to
    agree. Showing the raw descriptor after a rename reads as though the
    rename never took.
    """
    return str(merchant or merchant_name or name or "")


def format_duration_ms(milliseconds: float | None) -> str:
    """How long something took, for a person: ``0.8s``, ``13.4s``,
    ``1m 03s``. Blank stays blank.

    Seconds rather than the raw milliseconds a timer hands you: nobody
    reads 13411.9 as thirteen seconds, and the number that matters is
    how long the wait felt.
    """
    if not milliseconds:
        return ""
    seconds = milliseconds / 1000
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, rest = divmod(seconds, 60)
    return f"{int(minutes)}m {rest:02.0f}s"


def format_date_range(start: object, end: object) -> str:
    """A span short enough to sit on ONE line: ``Jul 29 to Aug 6, 2026``.

    Both ends in full, except a repeated year: that is what a same-year
    range can afford to lose, since whatever names the span (a file, a
    heading) carries it. A range that crosses a year keeps both, because
    that is exactly when the year is the surprising part.
    """
    left, right = format_date(start), format_date(end)
    if not right or left == right:
        return left or right
    if left[-4:].isdigit() and left[-4:] == right[-4:]:
        left = left[:-6]
    return f"{left} to {right}"


def _coarse_age(seconds: float) -> str:
    """Days / months / years for durations past the sub-day branches.

    Rounds to the NEAREST unit rather than truncating: six calendar
    months is 181 days, and ``int(181 / 30.44)`` is 5, so truncation
    reports a gap a whole month shorter than the one a calendar shows.
    Each unit still floors at 1, so a duration that reached this branch
    never reports as zero of anything.
    """
    days = int(seconds / 86400)
    if days < 30:
        return f"{days} day{'s' if days != 1 else ''} ago"
    if days < 365:
        months = max(1, round(days / 30.44))
        return f"{months} month{'s' if months != 1 else ''} ago"
    years = max(1, round(days / 365.25))
    return f"{years} year{'s' if years != 1 else ''} ago"


def format_relative_time(
    iso_str: str | None, *, now: datetime | None = None, coarse: bool = False
) -> str:
    """Format an ISO timestamp as a relative duration ("3 minutes ago").

    Returns ``"—"`` for empty input. Sub-minute durations render as
    ``"just now"``. Anything a day or older falls back to a short
    absolute format (``"%b %d %H:%M"``). On parse failure the raw input
    is returned so the value stays debuggable in the UI rather than
    silently disappearing.

    ``coarse`` keeps counting in days, months and years past that point
    instead, for ages that are naturally measured in months (when a model
    was pulled, say) where an absolute timestamp answers a question
    nobody asked. Off by default, so existing callers are unaffected.

    Tolerates missing timezone (assumed UTC) and a trailing ``Z`` (which
    Python's ``fromisoformat`` rejects pre-3.11).

    ``now`` is exposed for testability; production callers pass it as
    ``None`` so we default to ``datetime.now(timezone.utc)``.
    """
    if not iso_str:
        return "—"
    try:
        ts = iso_str.replace("Z", "+00:00") if "Z" in iso_str else iso_str
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        now_dt = now if now is not None else datetime.now(UTC)
        seconds = (now_dt - dt).total_seconds()
        if seconds < 60:
            return "just now"
        if seconds < 3600:
            mins = int(seconds / 60)
            return f"{mins} minute{'s' if mins != 1 else ''} ago"
        if seconds < 86400:
            hours = int(seconds / 3600)
            return f"{hours} hour{'s' if hours != 1 else ''} ago"
        if coarse:
            return _coarse_age(seconds)
        return dt.strftime("%b %d %H:%M")
    except (ValueError, TypeError, IndexError):
        return str(iso_str)


# What free costs, spelled once. Not ``$0``: trailing zeros are what
# make a figure read as a PRICE, and "$0" beside "$3 / $15" reads like a
# value that failed to load. Shared by the model picker (a local model's
# row) and a finished message's footer (a local model's turn).
FREE = "$0.00"
