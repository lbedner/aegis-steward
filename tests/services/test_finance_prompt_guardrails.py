"""What she says about herself (#273).

On a realtime call she read her own context check aloud ("256 older
messages fell out of my current view"). How she works - her context,
prompt, tools, sandbox - is not something to volunteer, anywhere she
talks. One rule, in her base chat prompt (which her voice agent and a
realtime engine's prompt extend) and in GPT-Live's own persona.
"""

from __future__ import annotations

from app.services.finance.domains.detection.analyst.prompts import (
    ABOUT_YOURSELF,
    FINANCE_CHAT_SYSTEM_PROMPT,
    FINANCE_LIVE_INSTRUCTIONS,
)


def test_her_base_prompt_keeps_the_machinery_to_herself() -> None:
    assert ABOUT_YOURSELF in FINANCE_CHAT_SYSTEM_PROMPT


def test_gpt_live_keeps_it_to_itself_too() -> None:
    assert ABOUT_YOURSELF in FINANCE_LIVE_INSTRUCTIONS


def test_she_can_still_explain_it_when_asked() -> None:
    assert "unless they ask" in ABOUT_YOURSELF
