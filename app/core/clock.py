"""The one clock the app stamps rows with."""

from __future__ import annotations

from datetime import UTC, datetime


def utcnow() -> datetime:
    """UTC, stored naive, for SQLite/Postgres parity: a local-time stamp
    reads differently depending on where the process runs."""
    return datetime.now(UTC).replace(tzinfo=None)
