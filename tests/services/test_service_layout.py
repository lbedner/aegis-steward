"""The service layout rules every conformant service is held to.

Behavioural tests cannot see structure: a read that drifts out of its
package's ``queries`` module still returns the same rows. These
assertions are where the standard is stated, so they fail on the drift.
"""

from __future__ import annotations

import pathlib

import pytest

CONFORMANT_SERVICES = ("ai", "comms", "finance", "insights", "rag")

# One-shot loaders, not runtime code: exempt by decision, whether a
# folder or a single module.
EXEMPT = {"seed", "seeds", "demo_seed", "fixtures"}

# Steward already owns these reads outside query modules. Keep this exact
# baseline while moving them separately; new files must follow the rule.
LEGACY_READS = {
    "ai": {
        "usage_recording.py",
        "service/usage.py",
        "domains/llm/llm_service.py",
        "domains/chat/conversation.py",
        "domains/chat/llm_catalog_context.py",
        "domains/chat/user_memory.py",
    },
    "finance": {
        "jobs.py",
        "ai_write_tools.py",
        "domains/ledger/subjects.py",
        "domains/ledger/merchants.py",
        "domains/planning/envelope_tags.py",
        "domains/writes/terms.py",
        "domains/writes/queue.py",
    },
}


@pytest.mark.parametrize("service", CONFORMANT_SERVICES)
def test_every_read_lives_in_a_queries_module(service: str) -> None:
    """Reads belong to a package's ``queries`` module; domain modules own
    writes and orchestration. A ``.exec(`` anywhere else is a read the
    next N+1 audit cannot find."""
    package = pytest.importorskip(f"app.services.{service}")
    root = pathlib.Path(package.__file__).parent
    offenders = [
        str(path.relative_to(root))
        for path in root.rglob("*.py")
        if ".exec(" in path.read_text()
        and path.name != "queries.py"
        and "queries" not in path.parent.name
        and not EXEMPT & set(path.with_suffix("").parts)
    ]
    new_offenders = sorted(set(offenders) - LEGACY_READS.get(service, set()))
    assert not new_offenders, (
        f"{service}: reads outside queries modules: {new_offenders}"
    )
