"""Trace assembly: the run trail's entries survive their own size caps.

The result field is clipped for display, so anything the UI must PARSE
(the pending-change card payload) rides a separate compact marker that
no cap can corrupt - a 9-row batch once rendered no card because its
JSON was truncated mid-string.
"""

import json
from types import SimpleNamespace
from typing import Any


def _result_event(tool_name: str, content: Any) -> Any:
    return SimpleNamespace(
        part=SimpleNamespace(tool_name=tool_name, content=content, metadata=None)
    )


class TestProposalComponentMarker:
    def test_an_oversized_batch_result_still_carries_its_marker(self) -> None:
        from app.services.ai.service.trace import record_tool_result

        items = [
            {
                "id": n,
                "pending_change_id": n,
                "status": "pending",
                "display": [{"label": "Transaction", "value": "X" * 300}],
            }
            for n in range(9)
        ]
        result = {
            "batch_id": "b-123",
            "change_type": "transaction.tag",
            "title": "Tag a transaction",
            "count": 9,
            "items": items,
        }
        trace: list[dict[str, Any]] = [{"tool": "propose_many", "args": ""}]

        record_tool_result(trace, _result_event("propose_many", json.dumps(result)))

        entry = trace[0]
        assert len(entry["result"]) <= 2_000  # the display clip stands
        marker = entry["component"]
        assert marker["kind"] == "pending_change_batch"
        assert marker["batch_id"] == "b-123"
        assert marker["title"] == "Tag a transaction"

    def test_a_single_proposal_gets_its_marker_too(self) -> None:
        from app.services.ai.service.trace import record_tool_result

        result = {
            "pending_change_id": 7,
            "change_type": "transaction.categorize",
            "title": "Categorize a transaction",
            "status": "pending",
            "display": [],
        }
        trace: list[dict[str, Any]] = [{"tool": "propose", "args": ""}]

        record_tool_result(trace, _result_event("propose", json.dumps(result)))

        marker = trace[0]["component"]
        assert marker["kind"] == "pending_change"
        assert marker["pending_change_id"] == 7

    def test_a_failed_proposal_carries_no_marker(self) -> None:
        from app.services.ai.service.trace import record_tool_result

        result = {"error": "unknown change type", "registered_change_types": []}
        trace: list[dict[str, Any]] = [{"tool": "propose", "args": ""}]

        record_tool_result(trace, _result_event("propose", json.dumps(result)))

        assert "component" not in trace[0]

    def test_other_tools_are_untouched(self) -> None:
        from app.services.ai.service.trace import record_tool_result

        trace: list[dict[str, Any]] = [{"tool": "run_code", "code": "x"}]

        record_tool_result(trace, _result_event("run_code", '{"output": "6"}'))

        assert "component" not in trace[0]


class TestPendingListingMarker:
    """``pending()`` is how the assistant shows a card again: its result
    lists cards, and each becomes the same identity marker a propose
    result carries, so the UI redraws cards it already knows how to."""

    def _listing(self) -> dict[str, Any]:
        card = {"change_type": "transaction.categorize", "title": "Categorize"}
        return {
            "pending": [
                {"batch_id": "b-1", "pending_change_ids": [84, 85], "rows": 2, **card},
                {"batch_id": None, "pending_change_ids": [7], "rows": 1, **card},
            ],
            "decided": [],
        }

    def test_one_marker_per_card(self) -> None:
        from app.services.ai.service.trace import record_tool_result

        trace: list[dict[str, Any]] = [{"tool": "pending", "args": ""}]
        record_tool_result(trace, _result_event("pending", json.dumps(self._listing())))

        batch, single = trace[0]["component"]
        assert batch == {
            "kind": "pending_change_batch",
            "batch_id": "b-1",
            "change_type": "transaction.categorize",
            "title": "Categorize",
            "count": 2,
        }
        assert single["kind"] == "pending_change"
        assert single["pending_change_id"] == 7

    def test_decided_cards_are_redrawn_too(self) -> None:
        """A rejected card the user asks about comes back as a card in
        its resolved state, not as a sentence about it."""
        from app.services.ai.service.trace import record_tool_result

        listing = {
            "pending": [],
            "decided": [
                {
                    "batch_id": "b-1",
                    "pending_change_ids": [84, 85, 86],
                    "rows": 3,
                    "change_type": "transaction.categorize",
                    "title": "Categorize",
                    "status": "rejected",
                }
            ],
        }
        trace: list[dict[str, Any]] = [{"tool": "pending", "args": ""}]
        record_tool_result(trace, _result_event("pending", json.dumps(listing)))

        (batch,) = trace[0]["component"]
        assert batch["kind"] == "pending_change_batch"
        assert batch["count"] == 3

    def test_an_empty_listing_carries_no_marker(self) -> None:
        from app.services.ai.service.trace import record_tool_result

        trace: list[dict[str, Any]] = [{"tool": "pending", "args": ""}]
        record_tool_result(
            trace, _result_event("pending", '{"pending": [], "decided": []}')
        )

        assert "component" not in trace[0]


class TestTheToolNominatesWhatIsDrawn:
    """Reading what you filed should not repaint the thread.

    ``pending()`` used to draw everything it listed, and it lists every
    card the agent filed inside a fourteen-day window - 34 of them on a
    real ledger. Its own docstring tells the model to call it before
    filing a replacement, so the blanket call is the common path. One
    turn came back with seven cards, six of them decided weeks earlier
    (2026-09-11).

    The tool decides now, because it is the only place that knows
    whether the user asked about something. The trace marks what it
    nominated and infers nothing.
    """

    def _card(self, batch: str, **over: Any) -> dict[str, Any]:
        return {
            "batch_id": batch,
            "pending_change_ids": [1, 2],
            "rows": 2,
            "change_type": "transaction.split",
            "title": "Split a transaction",
            **over,
        }

    def test_only_what_the_tool_nominated_is_drawn(self) -> None:
        from app.services.ai.service.trace import record_tool_result

        listing = {
            "pending": [self._card("b-new")],
            "decided": [self._card("b-old"), self._card("b-older")],
            "draw": [self._card("b-new")],
        }
        trace: list[dict[str, Any]] = [{"tool": "pending", "args": ""}]
        record_tool_result(trace, _result_event("pending", json.dumps(listing)))

        drawn = [m["batch_id"] for m in trace[0]["component"]]
        assert drawn == ["b-new"], "a decided card is history, not an offer"

    def test_nominating_nothing_draws_nothing(self) -> None:
        """A listing the model read but the user did not ask to see."""
        from app.services.ai.service.trace import record_tool_result

        listing = {
            "pending": [self._card("b-1")],
            "decided": [self._card("b-2")],
            "draw": [],
        }
        trace: list[dict[str, Any]] = [{"tool": "pending", "args": ""}]
        record_tool_result(trace, _result_event("pending", json.dumps(listing)))

        assert "component" not in trace[0]

    def test_a_listing_from_before_the_change_still_draws(self) -> None:
        """Stored traces have no ``draw``; their cards must keep
        rendering rather than vanish from old conversations."""
        from app.services.ai.service.trace import record_tool_result

        listing = {"pending": [self._card("b-1")], "decided": []}
        trace: list[dict[str, Any]] = [{"tool": "pending", "args": ""}]
        record_tool_result(trace, _result_event("pending", json.dumps(listing)))

        assert [m["batch_id"] for m in trace[0]["component"]] == ["b-1"]
