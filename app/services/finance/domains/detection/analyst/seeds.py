"""The database handoff: agent + memory-module seed rows and their loader."""

from collections.abc import Sequence
from typing import Any

from sqlmodel import Session, col, select

from app.core.log import logger
from app.core.seed import missing_rows, seed_rows
from app.services.ai.domains.chat.agent_registry import seed_agent
from app.services.ai.models.agents import (
    Agent,
    AgentTool,
    Tool,
)

# Registers the finance host tools (ledger/accounts/quote) by import, so a
# bare seeding process gets their ``tool`` rows and the grants below
# resolve. Same reason the ai fixtures import their built-ins.
import app.services.finance.ai_tools  # noqa: F401
from app.services.finance.domains.detection.analyst.prompts import (
    ANALYST_MAX_TOKENS,
    ANALYST_SYSTEM_PROMPT,
    ANALYST_TEMPERATURE,
    DEEP_DIVE_MAX_TOKENS,
    DEEP_DIVE_SYSTEM_PROMPT,
    DEEP_DIVE_TEMPERATURE,
    FINANCE_CHAT_MAX_TOKENS,
    FINANCE_CHAT_SYSTEM_PROMPT,
    FINANCE_CHAT_TEMPERATURE,
    FINANCE_VOICE_MAX_TOKENS,
    FINANCE_VOICE_SYSTEM_PROMPT,
    FINANCE_VOICE_TEMPERATURE,
)
from app.services.finance.domains.detection.analyst.shared import (
    AMAZON_EXPORTS_MODULE_SLUG,
    ANALYST_AGENT_SLUG,
    DEEP_DIVE_AGENT_SLUG,
    FINANCE_CHAT_AGENT_SLUG,
    FINANCE_VOICE_AGENT_SLUG,
    SNAPSHOT_MODULE_SLUG,
)
import app.services.insurance.ai_tools  # noqa: F401
import app.services.matters.ai_tools  # noqa: F401

SNAPSHOT_TOKEN_ESTIMATE = 1_200


def snapshot_module_definition() -> dict[str, Any]:
    """The seed row for the snapshot memory module."""
    return {
        "slug": SNAPSHOT_MODULE_SLUG,
        "name": "Finance snapshot",
        "description": (
            "Balances, net-worth trend, credit card and loan detail, portfolio "
            "positions, monthly cashflow, category spend against its norm, "
            "recent transactions, open anomalies, the cash forecast, and what "
            "is due next."
        ),
        "category": "finance",
        "prompt_content": None,
        "fetch_function": SNAPSHOT_MODULE_SLUG,
        "context_key": SNAPSHOT_MODULE_SLUG,
        "priority": 10,
        "token_estimate": SNAPSHOT_TOKEN_ESTIMATE,
        "is_active": True,
    }


AMAZON_EXPORTS_TEXT = """\
If the user asks about Amazon purchases or wants to match Amazon charges
to what was bought, point them to Amazon's data request page:
https://www.amazon.com/hz/privacy-central/data-requests/preview.html
For itemized purchases, request "Your Orders" (products, prices paid,
delivery dates, returns). For Prime, Subscribe & Save, Kindle Unlimited and
other recurring charges, request "Subscriptions" (status, billing period,
price, card used). Amazon emails a download link once the export is ready."""


def amazon_exports_module_definition() -> dict[str, Any]:
    """The seed row for where Amazon's order and subscription exports live."""
    return {
        "slug": AMAZON_EXPORTS_MODULE_SLUG,
        "name": "Amazon data exports",
        "description": "Where to request Amazon order and subscription exports",
        "category": "finance",
        "prompt_content": AMAZON_EXPORTS_TEXT,
        "fetch_function": None,
        "context_key": AMAZON_EXPORTS_MODULE_SLUG,
        "priority": 50,
        "token_estimate": 110,
        "is_active": True,
    }


def memory_module_definitions() -> tuple[dict[str, Any], ...]:
    """Every memory module the app seeds. Which agent reads one is the
    agent definition's ``memory_modules``, not the module's concern."""
    return (snapshot_module_definition(), amazon_exports_module_definition())


def analyst_agent_definition() -> dict[str, Any]:
    """The seed row for the finance analyst agent.

    ``model_id`` is None so the agent follows whatever model the AI service is
    configured with. Point it at one pulled Ollama tag by setting that column.
    """
    return {
        "slug": ANALYST_AGENT_SLUG,
        "name": "Finance Analyst",
        "description": "Writes the daily finance note from detected findings",
        "category": "finance",
        "model_id": None,
        "system_prompt": ANALYST_SYSTEM_PROMPT,
        "temperature": ANALYST_TEMPERATURE,
        "max_tokens": ANALYST_MAX_TOKENS,
        "memory_modules": [SNAPSHOT_MODULE_SLUG],
        "knowledge_base_ids": [],
        "is_active": True,
    }


def deep_dive_agent_definition() -> dict[str, Any]:
    """The seed row for the on-request deep dive.

    A SEPARATE agent row rather than a mode on the daily one, so it can be
    pointed at a bigger model and given a larger token budget without
    changing what runs nightly. Both read the same memory module.
    """
    return {
        "slug": DEEP_DIVE_AGENT_SLUG,
        "name": "Finance Analyst (deep dive)",
        "description": "On-request full review: situation, causes, findings triage, options",
        "category": "finance",
        "model_id": None,
        "system_prompt": DEEP_DIVE_SYSTEM_PROMPT,
        "temperature": DEEP_DIVE_TEMPERATURE,
        "max_tokens": DEEP_DIVE_MAX_TOKENS,
        "memory_modules": [SNAPSHOT_MODULE_SLUG],
        "knowledge_base_ids": [],
        "is_active": True,
    }


# The chat agent's data surface: the finance host tools registered by
# ``app.services.finance.ai_tools``. Attachment is by name against the
# tool rows the fixture sync seeds; a missing row is skipped, never an
# error, so seed order cannot brick the chat tab.
FINANCE_CHAT_TOOL_NAMES = (
    "ledger",
    # The rows, when the question names one: a payee, an amount, a date.
    # Without it, matching a receipt to a charge meant pulling months of
    # ledger into the sandbox and filtering in Python - 23 of one
    # session's 55 code blocks were exactly that.
    "transactions",
    "accounts",
    "quote",
    # Cash walked forward over any window the question names. Without
    # it the only forward-looking number was the fixed 60-day figure in
    # the briefing, so "the balance six months out" got hand-rolled
    # from the bill list and then disowned as untrustworthy.
    "projection",
    # The ids a proposal's payload takes - names alone cannot propose.
    "categories",
    # The bill surface: live streams, and the ranked shortlist of
    # unclaimed payments the app's own match picker uses - matches are
    # proposed FROM the shortlist, never from lookalike guessing.
    "bills",
    "bill_candidates",
    # The limits the user actually SET, and spend against them. Asked
    # "what is our budget for Medicine/Drugs?" the agent answered that
    # the budget target "isn't exposed here" - correctly: every other
    # tool reports what was spent, and a limit is a number the user
    # chose that lives nowhere else.
    "budget",
    # The tag directory: the label axis a transaction.tag payload
    # names; listed so spellings are reused, not coined per turn.
    "tags",
    # The one memory write: durable facts the user states about their
    # money (a property value, a bill no connection reports) outlive
    # the conversation.
    "save_memory",
    # A fact that changed is rewritten, and one a tool can now read is
    # dropped - memory is for what nothing else can re-read.
    "update_memory",
    "forget_memory",
    # The one LEDGER write, and it does not write: it files a pending
    # change the user approves in the app (FW-05). Registered
    # native_write, so it surfaces as its own visible call.
    "propose",
    # Same contract, many rows, one approval card with per-row veto.
    "propose_many",
    # Propose's cleanup half: see your OWN open cards, and retract the
    # ones a new proposal supersedes, instead of asking the user to.
    "pending",
    "withdraw",
    "withdraw_batch",
    # A misread on a pending card put right when the user says so
    # ("it's Holy Cow"), across every card that has it (issue 420).
    "revise",
    # Durable extraction: what was read out of an ephemeral image must
    # be recorded before it is answered from - the recording is what
    # later turns get instead of the pixels.
    "record_reading",
    # A chart, ranking, comparison or table under the answer (#266),
    # drawn from inside run_code with the rows the script computed.
    "draw_card",
    # The text the user pasted, read back on demand. A pasted page
    # stands in the conversation as a one-line marker; without this
    # there is no way to reach what it stands for.
    "pasted",
    # Her own context window, reported rather than guessed at. History
    # is budgeted by size and the oldest turns drop silently, so an
    # agent that has lost four pasted pages answers "no match found" in
    # exactly the voice of one that still has them.
    "context",
    # The case surface (ST-12). A matter is invisible most of the year
    # and urgent for eight days, which is exactly what a tool is for and
    # exactly what the snapshot must not carry.
    "parties",
    "matters",
    # What is outstanding, by deadline, with what each item is answered
    # by - the three piles of the renewal said out loud.
    "requests",
    # And the numbers with their sources, because a figure without its
    # provenance is not fit to put on a government form.
    "facts",
    # The paper itself, by the id the three above hand back: the letter
    # behind a request, read to the person who asked what it says.
    "paper",
    # And the shelf by NAME, because nobody knows a document's number.
    # Without this the only findable paper is paper already attached to
    # somebody, so the documents most needing attention - the ones with
    # no sender recorded - are the exact ones she cannot see.
    "documents",
    # How to reach a contact, read off their own filed letterhead. The
    # read half of contact.amend: without it she proposes from memory.
    "contact_details",
    # And for an organization with no paper at all: her guess at their
    # domain, CHECKED by fetching it. The app confirms; she never
    # reports a domain it refused.
    "look_up_contact",
    # Policies and the claims on them, with the ids claim.record names.
    "policies",
    "claim_candidates",
)


def finance_chat_agent_definition() -> dict[str, Any]:
    """The seed row for the conversational finance assistant.

    Same snapshot briefing as the analyst, warmer sampling, and
    ``code_mode`` on: chat questions that need arithmetic are computed in
    the sandbox over the finance tools rather than estimated in prose.
    """
    return {
        "slug": FINANCE_CHAT_AGENT_SLUG,
        "name": "Finance Assistant",
        "description": "Conversational finance chat with computed answers",
        "category": "finance",
        "model_id": None,
        "system_prompt": FINANCE_CHAT_SYSTEM_PROMPT,
        "temperature": FINANCE_CHAT_TEMPERATURE,
        "max_tokens": FINANCE_CHAT_MAX_TOKENS,
        "memory_modules": [SNAPSHOT_MODULE_SLUG, AMAZON_EXPORTS_MODULE_SLUG],
        "knowledge_base_ids": [],
        "is_active": True,
        "code_mode": True,
    }


# None follows the active model, as the chat agent does. Timed on her real
# context (2026-09-25, "How much is left in Vanessa's envelope?"): only
# gpt-5.6-luna (7-10s) and gpt-5-mini (12s) answered right. gpt-4.1-mini,
# gpt-4.1-nano, gpt-5.4-mini and gpt-5.4-nano were 2-6s and all wrong - they
# could not drive 31 tools in code mode, and said the envelope was missing
# or empty. A faster voice has to come from a smaller context, not a
# smaller model.
FINANCE_VOICE_MODEL: str | None = None


def finance_voice_agent_definition() -> dict[str, Any]:
    """The seed row for Illiana when spoken to: she EXTENDS the chat agent,
    so her prompt, tools and memory come from it at resolve time and an
    edit there reaches both. Only the spoken section, the model and the
    sampling are this row's own; tools and memory are left empty on
    purpose so they are inherited."""
    return {
        "slug": FINANCE_VOICE_AGENT_SLUG,
        "name": "Finance Assistant (voice)",
        "description": "The finance assistant, answering aloud",
        "category": "finance",
        "extends": FINANCE_CHAT_AGENT_SLUG,
        "model_id": FINANCE_VOICE_MODEL,
        "system_prompt": FINANCE_VOICE_SYSTEM_PROMPT,
        "temperature": FINANCE_VOICE_TEMPERATURE,
        "max_tokens": FINANCE_VOICE_MAX_TOKENS,
        "memory_modules": [],
        "knowledge_base_ids": [],
        "is_active": True,
        "code_mode": True,
    }


def finance_agent_definitions() -> tuple[dict[str, Any], ...]:
    """Every finance agent the app seeds. A seed only reaches a fresh
    install: after that the agent row is the prompt (#355)."""
    return (
        analyst_agent_definition(),
        deep_dive_agent_definition(),
        finance_chat_agent_definition(),
        finance_voice_agent_definition(),
    )


def _attach_memory_modules(
    session: Session, definitions: Sequence[dict[str, Any]]
) -> int:
    """Append to each existing agent the definition's modules it lacks.

    Additive only, like tool grants: what the row already lists stays, in
    order, so a module added to a definition reaches an agent seeded before
    it. The caller commits. Returns how many grants were added.
    """
    wanted = {
        d["slug"]: d["memory_modules"] for d in definitions if d["memory_modules"]
    }
    if not wanted:
        return 0
    added = 0
    for agent in session.exec(select(Agent).where(col(Agent.slug).in_(wanted))).all():
        current = list(agent.memory_modules or [])
        new = [slug for slug in wanted[agent.slug] if slug not in current]
        if new:
            agent.memory_modules = current + new
            session.add(agent)
            added += len(new)
    return added


def _attach_chat_tools(session: Session) -> int:
    """Link the chat agent to the finance tool rows (see ``app.core.seed``)."""
    agent = session.exec(
        select(Agent).where(Agent.slug == FINANCE_CHAT_AGENT_SLUG)
    ).first()
    if agent is None or agent.id is None:
        return 0
    tools = session.exec(
        select(Tool).where(col(Tool.name).in_(FINANCE_CHAT_TOOL_NAMES))
    ).all()
    for name in sorted(set(FINANCE_CHAT_TOOL_NAMES) - {t.name for t in tools}):
        logger.warning(f"Finance chat tool row missing, skipping: {name}")
    return seed_rows(
        session,
        AgentTool,
        "tool_id",
        [{"agent_id": agent.id, "tool_id": tool.id} for tool in tools],
        col(AgentTool.agent_id) == agent.id,
    )


def load_finance_agent_fixtures(session: Session) -> dict[str, int]:
    """Seed the finance agents (see ``app.core.seed``). Tool and
    memory-module grants are additive by name, so one a definition gained
    since an agent was seeded still reaches it."""
    definitions = finance_agent_definitions()
    counts = {"finance_agents": 0, "finance_module_links": 0, "finance_tool_links": 0}
    missing = missing_rows(session, Agent, "slug", definitions)
    for definition in missing:
        seed_agent(session, definition)
        counts["finance_agents"] += 1
        logger.info(f"Seeded agent '{definition['slug']}'")
    # A freshly seeded agent already lists its modules; only rows that
    # were there before can lack one.
    seeded = {d["slug"] for d in missing}
    present = [d for d in definitions if d["slug"] not in seeded]
    counts["finance_module_links"] = _attach_memory_modules(session, present)

    if any(counts.values()):
        session.commit()

    counts["finance_tool_links"] = _attach_chat_tools(session)
    if counts["finance_tool_links"]:
        session.commit()
        logger.info(
            f"Attached {counts['finance_tool_links']} tool(s) to the chat agent"
        )
    return counts
