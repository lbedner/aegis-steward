"""Her models, in one place (#273): the chat model and what a live call
runs on, in one dialog with one search. The catalog reads are stubbed
(the suite's catalog is empty); the live pick writes engine rows and the
active voice for real.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlmodel import select as query

from app.components.web_frontend.routes.chat_models import MODELS
from app.core.config import settings
from app.core.db import get_async_session
from app.services.ai.domains.voice import profiles
from app.services.ai.models.live_engine import LiveEngine
from app.services.ai.models.voice_profile import VoiceProfile
from tests._voice_catalog import TITLES, VOICE_MODELS, VOICED
from tests.web.dom import none, one, select, text, triggers

# The chat role's pick; each role marks its own.
CHAT_CURRENT = "button[data-kind=chat][aria-current=true]"
LIVE_CURRENT = "button[data-kind=realtime][aria-current=true]"


def _marked(html: str, selector: str, attr: str = "data-model-id") -> set[str]:
    return {b.get(attr) for b in select(html, selector)}


REALTIME = [m["model_id"] for m in VOICE_MODELS if m["mode"] == "realtime"]


@pytest.fixture
async def live(live_engine_rows: None, monkeypatch: pytest.MonkeyPatch) -> int:
    """An active voice on the default engine, in the database the app
    opens its own sessions on (the dialog holds no request session); a
    pick writes the running settings, which each test puts back. Returns
    the voice's id."""
    for key in profiles.SETTINGS.values():
        monkeypatch.setattr(settings, key, getattr(settings, key))
    monkeypatch.setattr(settings, "VOICE_LIVE_ENGINE", "gpt-live")
    async with get_async_session() as db:
        for row in await profiles.list_profiles(db):
            await db.delete(row)
        await db.flush()  # the deletes first: a unit of work inserts first
        voice = VoiceProfile(name="Illiana", is_active=True, live_engine="gpt-live")
        db.add(voice)
        await db.flush()
        assert voice.id is not None
        return voice.id


async def _engine_of(voice_id: int) -> str:
    async with get_async_session() as db:
        voice = await db.get(VoiceProfile, voice_id)
        assert voice is not None
        return voice.live_engine


@pytest.fixture
def catalog(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    import importlib

    llm = importlib.import_module("app.components.backend.api.llm.routes")
    from app.components.web_frontend.routes import chat_models as routes

    state: dict[str, Any] = {"active": "qwen2.5:7b", "picks": []}

    async def current() -> Any:
        return llm.CurrentConfigResponse(
            provider="ollama",
            model=state["active"],
            temperature=0.7,
            max_tokens=1024,
        )

    async def models(mode: str = "chat", **_: Any) -> list[Any]:
        if mode == "realtime":
            voiced = [(vendor, m) for vendor, m in VOICED if m["mode"] == "realtime"]
            # A routed copy of a live model: in the catalog, never callable.
            voiced.append(("google", {**voiced[-1][1], "model_id": "gemini/copy"}))
            return [
                llm.ModelResponse(
                    model_id=m["model_id"],
                    title=m["title"],
                    vendor=vendor,
                    context_window=m["context_window"],
                    input_price=None,
                    output_price=None,
                    released_on=None,
                    per_minute=0.05 if m["model_id"] == "gpt-live-1" else None,
                )
                for vendor, m in voiced
            ]
        return [
            llm.ModelResponse(
                model_id="qwen2.5:7b",
                title="Qwen 2.5 7B",
                vendor="ollama",
                family="qwen",
                context_window=32000,
                input_price=None,
                output_price=None,
                released_on="2025-01-01",
            ),
            llm.ModelResponse(
                model_id="gpt-oss:20b",
                title="GPT OSS 20B",
                vendor="ollama",
                family="gpt-oss",
                context_window=128000,
                input_price=None,
                output_price=None,
                released_on="2025-08-01",
            ),
            llm.ModelResponse(
                model_id="gpt-4o",
                title="OpenAI: GPT-4o",
                vendor="openai",
                family="gpt-4",
                context_window=128000,
                input_price=2.5,
                output_price=10.0,
                released_on="2024-05-13",
            ),
        ]

    async def vendors(**_: Any) -> list[Any]:
        return [
            llm.VendorResponse(name="ollama", model_count=2),
            llm.VendorResponse(name="openai", model_count=1, icon_b64="AAAA"),
        ]

    async def set_current(body: Any) -> Any:
        if body.model_id == "nope":
            from fastapi import HTTPException

            raise HTTPException(
                status_code=404, detail="Model 'nope' is not in the catalog."
            )
        state["active"] = body.model_id
        state["picks"].append(body.model_id)
        return llm.SetModelResponse(success=True, model_id=body.model_id, message="ok")

    monkeypatch.setattr(routes, "get_current", current)
    monkeypatch.setattr(routes, "get_models", models)
    monkeypatch.setattr(routes, "get_vendors", vendors)
    monkeypatch.setattr(routes, "set_current", set_current)
    return state


class TestTheChatRole:
    """The chat role reads the catalog through the LLM handlers; these
    stub the reads and exercise the shaping, the markup and the pick."""

    def test_the_composer_loads_the_chip(self, client: TestClient) -> None:
        loader = one(client.get("/chat").text, "#chat-composer #chat-model")
        assert loader.get("hx-get") == f"{MODELS}/chip"
        assert loader.get("hx-target") == "this"  # never the thread it sits in

    def test_chip_names_what_each_role_runs_on_and_opens_the_picker(
        self, hx: TestClient, catalog: dict[str, Any], live: int
    ) -> None:
        chip = one(hx.get(f"{MODELS}/chip").text, "button#chat-model")
        assert text(chip) == f"qwen2.5:7b · Live: {TITLES['gpt-live-1']}"
        assert chip.get("hx-get") == MODELS and chip.get("hx-target") == "#dialog-body"

    def test_vendor_sections_start_closed_and_mark_the_model(
        self, hx: TestClient, catalog: dict[str, Any]
    ) -> None:
        html = hx.get(MODELS, params={"show": "text"}).text
        sections = select(html, "details")
        assert [text(one(s, "summary .micro-label")) for s in sections] == [
            "ollama",
            "openai",
        ]
        # Closed, even the one holding the model in use: it leads the list
        # among the recently used instead.
        assert all(s.get("open") is None for s in sections)
        # One control row (the modality filter), and no summary above it:
        # the chip says what each role runs on, the checks mark it here.
        none(html, "input[name=mode]")
        none(html, "[data-roles]")
        assert _marked(html, CHAT_CURRENT) == {"qwen2.5:7b"}
        # Newest first within a section; the vendor prefix comes off under its own section.
        ollama_rows = [
            b.get("data-model-id") for b in select(sections[0], "button[data-model-id]")
        ]
        assert ollama_rows == ["gpt-oss:20b", "qwen2.5:7b"]
        gpt4o = one(sections[1], "button[data-model-id='gpt-4o']")
        assert text(one(gpt4o, "span span:first-child")) == "GPT-4o"
        assert "128k · $2.50 / $10" in text(gpt4o)
        one(sections[1], "summary img")  # the vendor icon rides the section

    def test_search_flattens_and_keeps_the_context(
        self, hx: TestClient, catalog: dict[str, Any]
    ) -> None:
        html = hx.get(MODELS, params={"q": "gpt"}).text
        none(html, "details")
        rows = select(html, "button[data-kind=chat]")
        assert [r.get("data-model-id") for r in rows] == ["gpt-oss:20b", "gpt-4o"]
        assert text(one(rows[1], "span span:first-child")) == "OpenAI: GPT-4o"

    def test_a_search_that_finds_nothing_says_so(
        self, hx: TestClient, catalog: dict[str, Any]
    ) -> None:
        assert "No models match" in hx.get(MODELS, params={"q": "zzz"}).text

    def test_typing_replaces_the_list_and_leaves_the_box_alone(
        self, hx: TestClient, catalog: dict[str, Any]
    ) -> None:
        """The rule every search here follows: a keystroke may swap the
        list, never the input being typed into. Swapping the input takes
        the caret and the focus with it, and the next keystroke lands
        nowhere."""
        dialog = hx.get(MODELS).text
        search = one(dialog, "#model-picker form[hx-get]")
        assert search.get("hx-target") == "#model-list"
        assert search.get("hx-select") == search.get("hx-target")
        assert search.get("hx-swap") == "outerHTML"
        one(dialog, "#model-list")

    def test_a_pick_switches_in_place_and_updates_the_chip(
        self, hx: TestClient, catalog: dict[str, Any]
    ) -> None:
        html = hx.post(MODELS, data={"model_id": "gpt-4o", "q": ""}).text
        assert catalog["picks"] == ["gpt-4o"]
        assert _marked(html, CHAT_CURRENT) == {"gpt-4o"}
        chip = one(html, "#chat-model[hx-swap-oob]")
        assert text(chip).startswith("gpt-4o")

    def test_a_refused_pick_changes_nothing_and_says_why(
        self, hx: TestClient, catalog: dict[str, Any]
    ) -> None:
        response = hx.post(MODELS, data={"model_id": "nope"})
        assert catalog["picks"] == []
        assert _marked(response.text, CHAT_CURRENT) == {"qwen2.5:7b"}
        none(response.text, "#chat-model")
        assert "not in the catalog" in triggers(response)["toast"]["text"]


class TestTheLiveRole:
    """What a live call runs on is picked from the same list: voice models
    sit with the rest, a filter narrows to a modality (#273)."""

    def test_each_role_marks_its_model_in_the_one_list(
        self, hx: TestClient, catalog: dict[str, Any], live: int
    ) -> None:
        html = hx.get(MODELS).text
        # Each role marks its own pick, in the one list (and again among the
        # recently used, when it leads there too).
        assert _marked(html, LIVE_CURRENT) == {"gpt-live-1"}
        assert _marked(html, CHAT_CURRENT) == {"qwen2.5:7b"}

    def test_voice_models_sit_with_the_rest_and_say_so(
        self, hx: TestClient, catalog: dict[str, Any], live: int
    ) -> None:
        html = hx.get(MODELS).text
        voice = select(html, "button[data-kind=realtime]")
        assert {b.get("data-model-id") for b in voice} == set(REALTIME)
        assert all(b.cssselect("[data-voice]") for b in voice)
        chat = select(html, "button[data-kind=chat]")  # the same list
        assert chat and not any(b.cssselect("[data-voice]") for b in chat)

    @pytest.mark.parametrize(
        ("show", "kinds"), [("voice", {"realtime"}), ("text", {"chat"})]
    )
    def test_a_filter_narrows_to_one_modality(
        self,
        hx: TestClient,
        catalog: dict[str, Any],
        live: int,
        show: str,
        kinds: set[str],
    ) -> None:
        html = hx.get(MODELS, params={"show": show}).text
        shown = {b.get("data-kind") for b in select(html, "button[data-kind]")}
        assert shown == kinds
        # A pick keeps the filter it was made under.
        assert {
            i.get("value") for i in select(html, "input[name=show][type=hidden]")
        } == {show}

    def test_every_vendor_a_call_reaches_and_only_its_own_ids(
        self, hx: TestClient, catalog: dict[str, Any], live: int
    ) -> None:
        """OpenAI's live models and Gemini's (through the relay); the
        catalog's routed copies ("gemini/...") are not callable."""
        html = hx.get(MODELS, params={"show": "voice"}).text
        ids = {
            b.get("data-model-id") for b in select(html, "button[data-kind=realtime]")
        }
        assert "gemini-3.8-live" in ids and "gpt-realtime-2.1" in ids
        assert "gemini/copy" not in ids

    def test_a_live_model_is_priced_by_the_minute(
        self, hx: TestClient, catalog: dict[str, Any], live: int
    ) -> None:
        html = hx.get(MODELS, params={"show": "voice"}).text
        rows = select(html, "button[data-model-id=gpt-live-1]")
        assert rows and all("$0.05/min" in text(row) for row in rows)

    def test_one_search_covers_every_role(
        self, hx: TestClient, catalog: dict[str, Any], live: int
    ) -> None:
        html = hx.get(MODELS, params={"q": "realtime"}).text
        none(html, "button[data-kind=chat]")
        found = select(html, "button[data-kind=realtime]")
        assert sorted(b.get("data-model-id") for b in found) == sorted(
            m for m in REALTIME if "realtime" in m
        )

    async def test_a_live_pick_is_what_the_next_call_runs_on(
        self,
        hx: TestClient,
        catalog: dict[str, Any],
        live: int,
    ) -> None:
        html = hx.post(
            MODELS, data={"model_id": "gpt-realtime-2.1", "kind": "realtime"}
        ).text

        assert await _engine_of(live) == "gpt-realtime-2.1"
        assert settings.VOICE_LIVE_ENGINE == "gpt-realtime-2.1"
        assert catalog["picks"] == []  # the chat model is untouched
        assert _marked(html, LIVE_CURRENT) == {"gpt-realtime-2.1"}
        chip = one(html, "#chat-model[hx-swap-oob]")
        assert text(chip).endswith(f"Live: {TITLES['gpt-realtime-2.1']}")

    # The dialog's read, then the pick's, then its re-render: on purpose.
    @pytest.mark.queryspy(threshold=4)
    async def test_a_model_on_two_engines_offers_each(
        self, hx: TestClient, catalog: dict[str, Any], live: int
    ) -> None:
        """GPT-Live runs hand-built or through Pydantic AI (#274): each
        engine is its own row, and a pick of one is the one that runs."""

        gpt_live = "button[data-model-id='gpt-live-1']"
        html = hx.get(MODELS, params={"q": "gpt-live"}).text
        assert _marked(html, gpt_live, "data-engine") == {
            "gpt-live",
            "gpt-live-pydantic",
        }

        html = hx.post(
            MODELS,
            data={
                "model_id": "gpt-live-1",
                "kind": "realtime",
                "engine": "gpt-live-pydantic",
            },
        ).text

        assert await _engine_of(live) == "gpt-live-pydantic"
        assert _marked(html, LIVE_CURRENT, "data-engine") == {"gpt-live-pydantic"}

    async def test_a_live_model_with_no_engine_gets_one(
        self,
        hx: TestClient,
        catalog: dict[str, Any],
        live: int,
    ) -> None:
        """Any realtime model in the catalog can be picked: its engine is
        made on the spot, talking like a call from the seeded one."""
        from app.services.ai.models.llm import LargeLanguageModel, LLMOrg
        from app.services.finance.domains.detection.analyst.live_engines import (
            ENGINE_SEEDS,
        )

        # The app's database lives for the whole run: a model of its own.
        model_id = f"gpt-realtime-{uuid4().hex[:8]}"
        async with get_async_session() as db:
            org = (await db.exec(query(LLMOrg).where(LLMOrg.slug == "openai"))).one()
            db.add(
                LargeLanguageModel(
                    model_id=model_id,
                    title="GPT Realtime",
                    mode="realtime",
                    served_by_org_id=org.id,
                )
            )

        hx.post(MODELS, data={"model_id": model_id, "kind": "realtime"})

        async with get_async_session() as db:
            made = (
                await db.exec(query(LiveEngine).where(LiveEngine.key == model_id))
            ).one()
        seeded = next(s for s in ENGINE_SEEDS if s["transport"] == "realtime")
        assert (made.transport, made.instructions, made.max_output_tokens) == (
            "realtime",
            seeded["instructions"],
            seeded["max_output_tokens"],
        )
        assert await _engine_of(live) == model_id

    async def test_a_model_the_catalog_lacks_is_refused(
        self,
        hx: TestClient,
        catalog: dict[str, Any],
        live: int,
    ) -> None:
        response = hx.post(MODELS, data={"model_id": "ghost", "kind": "realtime"})
        assert "not in the catalog" in triggers(response)["toast"]["text"]
        none(response.text, "#chat-model")
        assert await _engine_of(live) == "gpt-live"


class TestRecentlyUsed:
    """The last few models she ran lead the list, unlabelled: the ones
    you switch between are one click away (#273). From the usage ledger,
    so a model counts once it has been used, chat or call alike."""

    @pytest.fixture
    async def used(self) -> None:
        from datetime import datetime

        from app.services.ai.models.llm import LLMUsage

        # The app's database lives for the whole run; these are the newest.
        rows = [
            ("gpt-4o", datetime(2100, 1, 1)),
            ("gpt-realtime-2.1", datetime(2100, 1, 2)),
            ("not-in-the-list", datetime(2100, 1, 3)),
        ]
        async with get_async_session() as db:
            db.add_all(
                LLMUsage(
                    model_id=model_id,
                    timestamp=at,
                    action="test",
                    input_tokens=0,
                    output_tokens=0,
                    total_cost=0,
                )
                for model_id, at in rows
            )

    def test_the_latest_lead_the_list(
        self, hx: TestClient, catalog: dict[str, Any], live: int, used: None
    ) -> None:
        html = hx.get(MODELS).text
        recent = one(html, "#model-list [data-recent]")
        ids = [b.get("data-model-id") for b in select(recent, "button[data-model-id]")]
        # Each role's pick first, then the newest used; a model the list
        # does not hold is skipped.
        assert ids == ["qwen2.5:7b", "gpt-live-1", "gpt-realtime-2.1"]
        assert len(ids) <= 3
        # They lead, ahead of every group.
        first = one(html, "#model-list").getchildren()[0]
        assert first.get("data-recent") is not None

    def test_a_pick_leads_at_once_before_it_is_used(
        self, hx: TestClient, catalog: dict[str, Any], live: int, used: None
    ) -> None:
        """A model just picked has no usage yet; it still leads, checked,
        so the pick is seen - the groups below start closed."""
        html = hx.post(MODELS, data={"model_id": "gpt-oss:20b"}).text
        recent = one(html, "#model-list [data-recent]")
        ids = [b.get("data-model-id") for b in select(recent, "button[data-model-id]")]
        assert ids[0] == "gpt-oss:20b"
        assert "gpt-live-1" in ids  # the live pick leads too
        assert len(ids) <= 3
        marked = {b.get("data-model-id") for b in select(recent, CHAT_CURRENT)}
        assert marked == {"gpt-oss:20b"}

    def test_they_follow_the_filter(
        self, hx: TestClient, catalog: dict[str, Any], live: int, used: None
    ) -> None:
        html = hx.get(MODELS, params={"show": "text"}).text
        recent = one(html, "#model-list [data-recent]")
        kinds = {b.get("data-kind") for b in select(recent, "button[data-kind]")}
        assert kinds == {"chat"}

    def test_a_search_shows_only_what_it_found(
        self, hx: TestClient, catalog: dict[str, Any], live: int, used: None
    ) -> None:
        none(hx.get(MODELS, params={"q": "gpt"}).text, "[data-recent]")
