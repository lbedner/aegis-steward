"""Tests for the agent config loader (DB source, fallback, cache)."""

from collections.abc import Generator

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.ai.domains.chat.agent_loader import (
    DEFAULT_AGENT_SLUG,
    AgentConfig,
    agent_capabilities,
    default_agent_config,
    invalidate_agent_cache,
    resolve_agent,
)

# Registers save_memory and its kin, the native writes these tests split
# out of the sandbox: without it they pass in the full suite (another test
# imported it) and fail alone.
import app.services.ai.domains.chat.memory_tools  # noqa: F401
from app.services.ai.models import Agent, AgentTool, Tool


@pytest.fixture(autouse=True)
def clean_cache() -> Generator[None]:
    """Loader cache is module-global; keep tests independent."""
    invalidate_agent_cache()
    yield
    invalidate_agent_cache()


@pytest.fixture
def session(async_db_session: AsyncSession) -> AsyncSession:
    """The root conftest's transactional async session (rolled back per
    test). A bare local engine cannot create this project's schema-qualified
    tables (finance, scheduler, ...)."""
    return async_db_session


def _code_mode(*tool_names: str) -> AgentConfig:
    """A code-mode agent: the tests that use it vary only its tools."""
    return AgentConfig(
        slug="finance-assistant",
        name="Finance Assistant",
        system_prompt="...",
        model_id=None,
        temperature=0.4,
        max_tokens=900,
        tool_names=tool_names,
        code_mode=True,
    )


async def _add_agent(session: AsyncSession, **overrides: object) -> Agent:
    data: dict[str, object] = {
        "slug": "support",
        "name": "Support",
        "system_prompt": "You are support.",
        "temperature": 0.2,
        "max_tokens": 512,
    }
    data.update(overrides)
    agent = Agent(**data)  # type: ignore[arg-type]
    session.add(agent)
    await session.commit()
    await session.refresh(agent)
    return agent


class TestResolveFromDb:
    async def test_resolves_row_to_config(self, session: AsyncSession) -> None:
        await _add_agent(session)

        config = await resolve_agent("support", session=session)

        assert config == AgentConfig(
            slug="support",
            name="Support",
            system_prompt="You are support.",
            model_id=None,
            temperature=0.2,
            max_tokens=512,
        )

    async def test_code_mode_flag_survives_resolution(
        self, session: AsyncSession
    ) -> None:
        await _add_agent(session, code_mode=True)

        config = await resolve_agent("support", session=session)

        assert config.code_mode is True

    async def test_code_mode_defaults_off(self, session: AsyncSession) -> None:
        await _add_agent(session)

        config = await resolve_agent("support", session=session)

        assert config.code_mode is False

    async def test_only_active_tools_are_exposed(self, session: AsyncSession) -> None:
        agent = await _add_agent(session)
        active = Tool(name="lookup")
        disabled = Tool(name="dangerous", is_active=False)
        session.add(active)
        session.add(disabled)
        await session.commit()
        # Commit expires instances; re-load before touching .id in async land.
        await session.refresh(agent)
        await session.refresh(active)
        await session.refresh(disabled)
        session.add(AgentTool(agent_id=agent.id, tool_id=active.id))
        session.add(AgentTool(agent_id=agent.id, tool_id=disabled.id))
        await session.commit()

        config = await resolve_agent("support", session=session)

        assert config.tool_names == ("lookup",)


class TestCodeModeSplitsReadsFromWrites:
    """Code mode sandboxes the data tools, never the memory writes.

    A tool inside the sandbox is called by generated Python; a write the
    user should be able to see in the tool trail has to stay a native
    call. This is also the seam the propose/approve queue needs later.
    """

    def _code_mode_tools(self, config: AgentConfig) -> list[str]:
        capabilities = agent_capabilities(config)
        code_mode = next(
            cap for cap in capabilities if type(cap).__name__ == "CodeMode"
        )
        return list(code_mode.tools)

    def test_write_tools_stay_native(self) -> None:
        sandboxed = self._code_mode_tools(
            _code_mode("ledger", "accounts", "save_memory")
        )

        assert "ledger" in sandboxed
        assert "accounts" in sandboxed
        assert "save_memory" not in sandboxed

    def test_a_registry_declared_native_write_stays_out_of_the_sandbox(
        self,
    ) -> None:
        """The tool declares its own nature at registration
        (native_write=True); the loader asks the registry instead of
        keeping a hardcoded list. This is how ``propose`` - the queue's
        one write tool - stays a visible native call."""
        from app.services.ai.domains.chat.tools import (
            register_tool,
            unregister_tool,
        )

        register_tool(
            "propose_test_tool",
            lambda: None,
            native_write=True,
            replace=True,
        )
        try:
            sandboxed = self._code_mode_tools(_code_mode("ledger", "propose_test_tool"))
        finally:
            unregister_tool("propose_test_tool")

        assert "ledger" in sandboxed
        assert "propose_test_tool" not in sandboxed

    def test_read_only_agent_sandboxes_everything(self) -> None:
        assert self._code_mode_tools(_code_mode("ledger", "accounts")) == [
            "ledger",
            "accounts",
        ]


class TestFallback:
    async def test_missing_row_falls_back_to_default(
        self, session: AsyncSession
    ) -> None:
        """Empty DB (memory-mode parity): the code default is the agent."""
        config = await resolve_agent(DEFAULT_AGENT_SLUG, session=session)

        assert config == default_agent_config()

    async def test_missing_row_is_not_cached(self, session: AsyncSession) -> None:
        """A row seeded after a fallback resolve must win the next resolve."""
        first = await resolve_agent("support", session=session)
        assert first == default_agent_config()

        await _add_agent(session)
        second = await resolve_agent("support", session=session)

        assert second.system_prompt == "You are support."

    async def test_inactive_agent_falls_back(self, session: AsyncSession) -> None:
        await _add_agent(session, is_active=False)

        config = await resolve_agent("support", session=session)

        assert config == default_agent_config()


class TestCache:
    async def test_config_is_cached_until_invalidated(
        self, session: AsyncSession
    ) -> None:
        agent = await _add_agent(session)

        first = await resolve_agent("support", session=session)
        assert first.system_prompt == "You are support."

        agent.system_prompt = "Updated."
        session.add(agent)
        await session.commit()

        stale = await resolve_agent("support", session=session)
        assert stale.system_prompt == "You are support."

        invalidate_agent_cache("support")
        fresh = await resolve_agent("support", session=session)
        assert fresh.system_prompt == "Updated."

    async def test_invalidate_all_clears_every_slug(
        self, session: AsyncSession
    ) -> None:
        agent = await _add_agent(session)
        await resolve_agent("support", session=session)

        agent.name = "Renamed"
        session.add(agent)
        await session.commit()

        invalidate_agent_cache()
        fresh = await resolve_agent("support", session=session)
        assert fresh.name == "Renamed"


async def _tool(session: AsyncSession, agent: Agent, name: str) -> None:
    tool = Tool(name=name)
    session.add(tool)
    await session.commit()
    await session.refresh(agent)
    await session.refresh(tool)
    session.add(AgentTool(agent_id=agent.id, tool_id=tool.id))
    await session.commit()


class TestExtends:
    """One level of inheritance (#260): a child agent is its parent with
    its own section in front and its own model and sampling. Illiana's
    voice agent extends her written one, so an edit to her prompt, tools
    or memory reaches both."""

    async def _family(self, session: AsyncSession, **child: object) -> Agent:
        parent = await _add_agent(
            session,
            slug="illiana",
            system_prompt="You are Illiana.",
            memory_modules=["finance_snapshot"],
            code_mode=True,
            model_id=None,
        )
        await _tool(session, parent, "ledger")
        data: dict[str, object] = {
            "slug": "illiana-voice",
            "system_prompt": "Answer aloud, briefly.",
            "extends": "illiana",
            "model_id": "gpt-4.1-mini",
            "temperature": 0.3,
            "max_tokens": 400,
        }
        data.update(child)
        return await _add_agent(session, **data)

    async def test_the_childs_section_comes_first(self, session: AsyncSession) -> None:
        await self._family(session)
        config = await resolve_agent("illiana-voice", session=session)
        # First, because an instruction at the end of a long prompt is the
        # one that gets ignored.
        assert config.system_prompt == "Answer aloud, briefly.\n\nYou are Illiana."

    async def test_tools_and_memory_are_inherited(self, session: AsyncSession) -> None:
        await self._family(session)
        config = await resolve_agent("illiana-voice", session=session)
        assert config.tool_names == ("ledger",)
        assert config.memory_modules == ("finance_snapshot",)

    async def test_model_and_sampling_are_the_childs_own(
        self, session: AsyncSession
    ) -> None:
        await self._family(session, code_mode=False)
        config = await resolve_agent("illiana-voice", session=session)
        assert config.model_id == "gpt-4.1-mini"
        assert (config.temperature, config.max_tokens) == (0.3, 400)
        assert config.code_mode is False
        assert config.slug == "illiana-voice"

    async def test_a_child_with_its_own_tools_keeps_them(
        self, session: AsyncSession
    ) -> None:
        child = await self._family(session, memory_modules=["voice_notes"])
        await _tool(session, child, "speak")
        config = await resolve_agent("illiana-voice", session=session)
        assert config.tool_names == ("speak",)
        assert config.memory_modules == ("voice_notes",)

    async def test_only_one_level(self, session: AsyncSession) -> None:
        await self._family(session)
        await _add_agent(
            session,
            slug="grandchild",
            system_prompt="Whisper.",
            extends="illiana-voice",
        )
        config = await resolve_agent("grandchild", session=session)
        # The chain is not followed: its parent's own section, not its
        # parent's parent.
        assert config.system_prompt == "Whisper.\n\nAnswer aloud, briefly."

    async def test_a_missing_parent_leaves_the_child_alone(
        self, session: AsyncSession
    ) -> None:
        await _add_agent(
            session, slug="orphan", system_prompt="Alone.", extends="nobody"
        )
        config = await resolve_agent("orphan", session=session)
        assert config.system_prompt == "Alone."
        assert config.tool_names == ()

    async def test_editing_the_parent_reaches_a_cached_child(
        self, session: AsyncSession
    ) -> None:
        await self._family(session)
        await resolve_agent("illiana-voice", session=session)
        parent = await resolve_agent("illiana", session=session)
        assert parent.system_prompt == "You are Illiana."

        row = await session.get(Agent, (await _row_id(session, "illiana")))
        assert row is not None
        row.system_prompt = "You are Illiana, revised."
        session.add(row)
        await session.commit()

        invalidate_agent_cache("illiana")
        child = await resolve_agent("illiana-voice", session=session)
        assert child.system_prompt.endswith("You are Illiana, revised.")


async def _row_id(session: AsyncSession, slug: str) -> int:
    from sqlmodel import select

    row = (await session.exec(select(Agent).where(Agent.slug == slug))).one()
    assert row.id is not None
    return row.id


class TestAWriteCalledFromAScript:
    """A write tool is not in the sandbox, so calling one from a script
    fails "Unknown function". Gemini Live did exactly that with propose
    on a call, retried the identical script and stalled (2026-10-01).
    The failure now says how to recover."""

    def test_the_hint_names_the_tool_to_call_directly(self) -> None:
        from app.services.ai.domains.chat.agent_loader import native_write_hint

        failed = "Runtime error:\nNameError: Unknown function: propose"
        hint = native_write_hint(failed, frozenset({"propose", "pending"}))
        assert hint is not None and "`propose`" in hint and "directly" in hint
        # the sandbox's checker can refuse it before it runs, too
        checked = "error[unresolved-reference]: Name `pending` used when not defined"
        assert "`pending`" in (native_write_hint(checked, frozenset({"pending"})) or "")
        assert (
            native_write_hint(
                "Runtime error:\nNameError: Unknown function: lookup_typo",
                frozenset({"propose"}),
            )
            is None
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("tool_names", "hinted"),
        [(("ledger", "save_memory"), True), (("ledger",), False)],
        ids=["granted", "not-granted"],
    )
    async def test_the_model_is_told_to_call_it_directly(
        self, tool_names: tuple[str, ...], hinted: bool
    ) -> None:
        """Only at a write this agent holds: a read-only agent pointed at
        save_memory would go after a tool it cannot call (PR #304 review)."""
        from pydantic_ai import Agent
        from pydantic_ai.messages import (
            ModelMessage,
            ModelResponse,
            RetryPromptPart,
            TextPart,
            ToolCallPart,
        )
        from pydantic_ai.models.function import AgentInfo, FunctionModel

        from app.services.ai.domains.chat.tools import resolve_tools

        config = _code_mode(*tool_names)
        told: list[str] = []

        def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            retries = [
                part
                for message in messages
                for part in getattr(message, "parts", [])
                if isinstance(part, RetryPromptPart)
            ]
            if not retries:
                return ModelResponse(
                    parts=[
                        ToolCallPart(
                            "run_code", {"code": 'await save_memory(text="x")'}
                        )
                    ]
                )
            told.append(str(retries[-1].content))
            return ModelResponse(parts=[TextPart("ok")])

        agent = Agent(
            FunctionModel(model),
            tools=resolve_tools(config.tool_names),
            capabilities=agent_capabilities(config),
        )
        await agent.run("remember x")

        assert told and "save_memory" in told[0]  # the sandbox's own error
        assert ("`save_memory` is its own tool" in told[0]) is hinted


def test_her_prompt_says_writes_are_their_own_tools() -> None:
    """Gemini Live called propose from inside run_code (2026-10-01): the
    prompt never said the writes are not in the sandbox."""
    from app.services.finance.domains.detection.analyst.prompts import (
        FINANCE_CHAT_SYSTEM_PROMPT,
    )

    assert "are NOT in the sandbox" in FINANCE_CHAT_SYSTEM_PROMPT
    assert "`propose`" in FINANCE_CHAT_SYSTEM_PROMPT
