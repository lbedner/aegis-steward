"""A tool's write waits its turn.

``retry_on_locked``'s own docstring names the case - "a proposal that
files a change" - and the propose tool never used it. Illiana read the
ledger, proposed a change, and the write could not upgrade the lock the
read had taken: "database is locked", mid-turn, with the answer already
half spoken.

Every tool write that COMMITS retries. The reads do not: a read that
cannot get a shared lock is a different problem and hiding it helps
nobody.
"""

import asyncio
from typing import Any

import pytest
from sqlalchemy.exc import OperationalError

from app.services.finance import ai_write_tools


def _locked() -> OperationalError:
    return OperationalError("INSERT", {}, Exception("database is locked"))


class TestEveryWritingToolRetries:
    @pytest.mark.parametrize(
        "name", ["propose", "propose_many", "withdraw", "withdraw_batch"]
    )
    def test_it_is_wrapped(self, name: str) -> None:
        """Named one by one rather than scanned, so adding a fifth
        writing tool is a decision somebody makes rather than one they
        forget."""
        import inspect

        source = inspect.getsource(getattr(ai_write_tools, name))
        assert "retry_on_locked" in source, f"{name} does not wait its turn"

    @pytest.mark.asyncio
    async def test_a_locked_first_try_is_retried_not_lost(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        tries = {"n": 0}

        async def flaky(*args: Any, **kwargs: Any) -> Any:
            tries["n"] += 1
            if tries["n"] == 1:
                raise _locked()
            raise ValueError("stopped here on purpose")

        from app.services.finance.domains import writes

        monkeypatch.setattr(writes, "propose", flaky)
        # Capture the real one first: patching with a lambda that calls
        # asyncio.sleep patches it with itself.
        real_sleep = asyncio.sleep
        monkeypatch.setattr(asyncio, "sleep", lambda _s: real_sleep(0))

        said = await ai_write_tools.propose("transaction.memo", {"x": 1})

        assert tries["n"] == 2  # the first attempt was retried, not surfaced
        assert "stopped here on purpose" in str(said)
