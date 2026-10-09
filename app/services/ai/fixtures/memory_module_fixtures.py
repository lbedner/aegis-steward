"""Memory-module seeding: rows only, in any service's definitions.

A module says nothing about which agent reads it; that is the agent
definition's ``memory_modules``. Services own their module rows and hand
them in here, so every module seeds through the one loader.
"""

from collections.abc import Sequence
from typing import Any

from sqlmodel import Session

from app.core.log import logger
from app.core.seed import seed_rows
from app.services.ai.models.agents import MemoryModule


def load_memory_module_fixtures(
    session: Session, definitions: Sequence[dict[str, Any]]
) -> dict[str, int]:
    """Seed the given memory modules (see ``app.core.seed``)."""
    added = seed_rows(session, MemoryModule, "slug", definitions)
    if added:
        session.commit()
        logger.info(f"Seeded {added} memory module(s)")
    return {"memory_modules": added}
