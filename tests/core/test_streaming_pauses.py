"""A quiet stream is not a broken one - say so while it is quiet.

Between the `connect` frame and the first token the SSE stream sent
nothing at all, so a 40-second Ollama model load looked exactly like a
hang: three pulsing dots and no way to tell waiting from dead. The same
silence covers any slow step - a cold provider, a long tool call, a
model thinking - so the fix is not an Ollama one. Anything the browser
follows can go quiet, and the cure is the same everywhere: a frame that
says how long the quiet has lasted.

Not a heartbeat - a heartbeat ticks regardless, and a stream that is
answering has nothing to explain. And not a chat helper: it lives in
core over a bare async iterator, because what goes quiet is the stream,
whatever is producing it.
"""

import asyncio

import pytest

from app.core.streaming import Waiting, announce_waiting


async def _quiet(delay: float, *items: str):
    """A source that stalls before its first item."""
    await asyncio.sleep(delay)
    for item in items:
        yield item


class TestASilentStreamSaysSo:
    @pytest.mark.asyncio
    async def test_a_stall_before_the_first_item_is_announced(self) -> None:
        """The Ollama shape: nothing at all until the model is loaded."""
        out = [f async for f in announce_waiting(_quiet(0.25, "hello"), every=0.05)]

        assert any(isinstance(f, Waiting) for f in out)
        assert out[-1] == "hello"

    @pytest.mark.asyncio
    async def test_a_stream_that_never_stalls_is_left_alone(self) -> None:
        """No heartbeat on a stream that is answering: the frame exists
        to explain a pause, and a stream with no pause has nothing to
        explain."""
        out = [f async for f in announce_waiting(_quiet(0, "a", "b"), every=5)]

        assert out == ["a", "b"]

    @pytest.mark.asyncio
    async def test_the_wait_is_measured_from_the_last_item_not_the_start(
        self,
    ) -> None:
        """A pause is how long THIS silence has lasted. Measuring from
        the start would have a mid-stream tool call report the whole
        turn's age, which says nothing about whether to keep waiting."""

        async def pause_in_the_middle():
            yield "a"
            await asyncio.sleep(0.25)
            yield "b"

        out = [f async for f in announce_waiting(pause_in_the_middle(), every=0.05)]

        waits = [f for f in out if isinstance(f, Waiting)]
        assert waits, "the mid-stream pause went unannounced"
        assert max(w.seconds for w in waits) < 1

    @pytest.mark.asyncio
    async def test_an_empty_stream_ends_rather_than_beating_forever(self) -> None:
        async def nothing():
            return
            yield  # pragma: no cover

        assert [f async for f in announce_waiting(nothing(), every=0.01)] == []

    @pytest.mark.asyncio
    async def test_the_source_failure_is_the_callers_to_see(self) -> None:
        """Wrapping must not swallow or delay the error that ends a
        turn - the browser's error frame depends on it arriving."""

        async def breaks():
            yield "a"
            raise RuntimeError("provider said no")

        with pytest.raises(RuntimeError, match="provider said no"):
            [f async for f in announce_waiting(breaks(), every=5)]


class TestTheStreamCarriesThePause:
    """The wiring, not just the helper: a `waiting` frame has to reach
    the browser, and the caption it fills has to exist in the partial
    rather than being built in JS."""

    def test_the_stream_endpoint_emits_a_waiting_frame(self) -> None:
        from pathlib import Path

        router = Path("app/components/backend/api/ai/router.py").read_text()
        assert "announce_waiting(" in router
        assert "event: waiting" in router

    def test_the_caption_lives_in_the_partial(self) -> None:
        """Markup a script needs lives in the template it belongs to;
        a JS string is a second home for the same thing."""
        from pathlib import Path

        macros = Path(
            "app/components/web_frontend/templates/components/macros/chat.html"
        ).read_text()
        script = Path("app/components/web_frontend/static/js/chat.js").read_text()
        assert "data-waiting" in macros
        assert "data-waiting" in script
        assert "<span" not in script.split("stillWaiting")[1][:400]


def _running_model(name: str):
    """A row exactly as ``/api/ps`` hands it back."""
    from datetime import datetime

    from app.services.ai.domains.llm.ollama import OllamaRunningModel

    return OllamaRunningModel(
        name=name,
        model=name,
        size=1,
        size_vram=1,
        digest="sha256:deadbeef",
        details={"families": None},
        expires_at=datetime(2026, 1, 1),
    )


class TestAPauseCanSayWhy:
    """Ollama evicts a model on ``keep_alive`` expiry and reloads it on
    the next request - tens of seconds of silence on a large model.
    "waiting 40s" is honest but alarming; "loading deepseek-r1 - 40s" is
    the same wait with the part that says it will finish."""

    @pytest.mark.asyncio
    async def test_the_explainer_is_asked_once_per_quiet_stretch(self) -> None:
        """Not once per frame: what a wait means is the caller's
        business, and whatever it costs to find out must not be paid
        every two seconds."""
        asked = 0

        async def explain() -> str:
            nonlocal asked
            asked += 1
            return "loading"

        out = [
            f
            async for f in announce_waiting(
                _quiet(0.3, "hi"), every=0.05, explain=explain
            )
        ]

        waits = [f for f in out if isinstance(f, Waiting)]
        assert len(waits) > 1, "expected several frames in one stretch"
        assert asked == 1
        assert all(w.reason == "loading" for w in waits)

    @pytest.mark.asyncio
    async def test_an_explainer_that_fails_does_not_fail_the_turn(self) -> None:
        """A missing reason must never turn a slow answer into a broken
        one - the wait still goes out, just unexplained."""

        async def explain() -> str:
            raise RuntimeError("ollama is not listening")

        out = [
            f
            async for f in announce_waiting(
                _quiet(0.15, "hi"), every=0.05, explain=explain
            )
        ]

        assert out[-1] == "hi"
        assert all(f.reason is None for f in out if isinstance(f, Waiting))


class TestTheOllamaReason:
    @pytest.mark.asyncio
    async def test_a_cold_model_is_named_as_loading(self, monkeypatch) -> None:
        from app.services.ai.domains.llm import waiting as waiting_module
        from app.services.ai.models import AIProvider

        class _Client:
            async def fetch_running_models(self):
                return []

        monkeypatch.setattr(waiting_module, "OllamaClient", _Client)

        reason = await waiting_module.waiting_reason(AIProvider.OLLAMA, "deepseek-r1")

        assert reason == "loading"

    @pytest.mark.asyncio
    async def test_a_warm_model_is_thinking_not_loading(self, monkeypatch) -> None:
        """The two waits are different in kind and only one ends on its
        own. The model NAME is deliberately not the answer: the
        composer's chip already shows it, so repeating it in the caption
        says nothing that is not already on screen.

        Built from the REAL /api/ps model, not a stub carrying
        whatever attribute the code happened to ask for. A hand-rolled
        double with a ``model_id`` on it is what let this ship reading a
        field ``OllamaRunningModel`` does not have: the explainer raised
        AttributeError, its own except swallowed it, and every pause went
        out unexplained while the tests stayed green."""
        from app.services.ai.domains.llm import waiting as waiting_module
        from app.services.ai.models import AIProvider

        warm = _running_model("deepseek-r1")

        class _Client:
            async def fetch_running_models(self):
                return [warm]

        monkeypatch.setattr(waiting_module, "OllamaClient", _Client)

        assert (
            await waiting_module.waiting_reason(AIProvider.OLLAMA, "deepseek-r1")
            == "thinking"
        )

    @pytest.mark.asyncio
    async def test_a_different_warm_model_does_not_count_as_this_one(
        self, monkeypatch
    ) -> None:
        from app.services.ai.domains.llm import waiting as waiting_module
        from app.services.ai.models import AIProvider

        class _Client:
            async def fetch_running_models(self):
                return [_running_model("llama3.2:3b")]

        monkeypatch.setattr(waiting_module, "OllamaClient", _Client)

        assert (
            await waiting_module.waiting_reason(AIProvider.OLLAMA, "deepseek-r1")
            == "loading"
        )

    @pytest.mark.asyncio
    async def test_a_provider_that_cannot_say_says_nothing(self) -> None:
        from app.services.ai.domains.llm.waiting import waiting_reason
        from app.services.ai.models import AIProvider

        assert await waiting_reason(AIProvider.OPENAI, "gpt-4o") is None


class TestTheSourceKeepsItsContext:
    """Racing each ``__anext__`` in its own task gave every step a
    FRESH copy of the context, so a ContextVar set while producing one
    item and reset while producing a later one straddled two contexts:

        <Token var=<ContextVar name='current_user_id'>> was created in
        a different Context

    Live, mid-answer, after the tools had already run - the turn died
    and the UI was left stuck in a streaming state. Async generators
    have no context of their own (PEP 568 was deferred), so they run in
    whichever context calls them, and the fix is to make that one
    context rather than one per item.
    """

    @pytest.mark.asyncio
    async def test_a_contextvar_survives_across_items(self) -> None:
        import contextvars

        var: contextvars.ContextVar[str] = contextvars.ContextVar("probe")

        async def sets_then_resets():
            token = var.set("in-flight")
            yield "first"
            await asyncio.sleep(0.15)  # long enough to force a heartbeat
            yield "second"
            var.reset(token)  # the line that raised
            yield "third"

        out = [
            f
            async for f in announce_waiting(sets_then_resets(), every=0.05)
            if not isinstance(f, Waiting)
        ]

        assert out == ["first", "second", "third"]
