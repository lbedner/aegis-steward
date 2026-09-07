"""
Activity event system for tracking system events.

Provides a pluggable event store with an in-memory default implementation.
Can be configured with alternative backends (Redis, database) via configure_store().
"""

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol


@dataclass
class ActivityEvent:
    """A single activity event."""

    component: str  # "database", "worker", "scheduler", etc.
    event_type: str  # "startup", "shutdown", "status_change", "error"
    message: str  # Human-readable message
    status: str  # "success", "warning", "error"
    timestamp: datetime = field(default_factory=datetime.now)
    details: str | None = None  # Optional detailed information


class ActivityEventStore(Protocol):
    """Protocol for activity event storage backends."""

    def add(self, event: ActivityEvent) -> None:
        """Add an event to the store."""
        ...

    def get_recent(self, limit: int = 10) -> list[ActivityEvent]:
        """Get recent events, newest first."""
        ...

    def clear(self) -> None:
        """Clear all events."""
        ...


class InMemoryEventStore:
    """In-memory event store using a bounded deque."""

    def __init__(self, max_events: int = 100) -> None:
        """Initialize the store with a maximum event capacity."""
        self._events: deque[ActivityEvent] = deque(maxlen=max_events)

    def add(self, event: ActivityEvent) -> None:
        """Add an event to the store (newest first)."""
        self._events.appendleft(event)

    def get_recent(self, limit: int = 10) -> list[ActivityEvent]:
        """Get recent events, newest first."""
        return list(self._events)[:limit]

    def clear(self) -> None:
        """Clear all events."""
        self._events.clear()


# Module-level singleton
_store: ActivityEventStore | None = None


def get_store() -> ActivityEventStore:
    """Get the activity event store (creates default if needed)."""
    global _store
    if _store is None:
        _store = InMemoryEventStore(max_events=100)
    return _store


def configure_store(store: ActivityEventStore) -> None:
    """
    Configure a custom activity store.

    Use this to swap the default in-memory store for Redis, database, etc.

    Example:
        activity.configure_store(RedisEventStore(redis_client))
    """
    global _store
    _store = store


def add_event(
    component: str,
    event_type: str,
    message: str,
    status: str = "success",
    details: str | None = None,
) -> None:
    """
    Add an activity event.

    Args:
        component: Component name (e.g., "database", "worker", "scheduler")
        event_type: Type of event (e.g., "startup", "shutdown", "status_change")
        message: Human-readable message
        status: Event status ("success", "warning", "error")
        details: Optional detailed information about the event
    """
    event = ActivityEvent(
        component=component,
        event_type=event_type,
        message=message,
        status=status,
        details=details,
    )
    get_store().add(event)


def get_recent(limit: int = 10) -> list[ActivityEvent]:
    """
    Get recent activity events.

    Args:
        limit: Maximum number of events to return

    Returns:
        List of recent events, newest first
    """
    return get_store().get_recent(limit)
