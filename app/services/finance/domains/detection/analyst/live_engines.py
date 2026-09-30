"""Illiana's live engines, seeded into ``live_engine`` when missing (#273).

Each names its model by catalog id (``large_language_model.model_id``);
the name, maker and price are the catalog's.

The rows are the app's once seeded: an edit (a tighter cap, other
instructions) is never overwritten. A new engine is a new entry here.
"""

from typing import Any

from app.services.finance.domains.detection.analyst.prompts import (
    FINANCE_LIVE_INSTRUCTIONS,
    FINANCE_REALTIME_INSTRUCTIONS,
)

# A realtime reply's hard cap. Output tokens include her speech's audio
# (~1,200 a minute) and any run_code script she writes, so this is a
# backstop against a monologue, not the brevity itself - the instructions
# are that.
REALTIME_REPLY_CAP = 1_200

ENGINE_SEEDS: tuple[dict[str, Any], ...] = (
    {
        "key": "gpt-live",
        "transport": "gpt_live",
        "model": "gpt-live-1",
        "note": "full duplex, hums while she works; billed by the minute plus her answers",
        "instructions": FINANCE_LIVE_INSTRUCTIONS,
        "sort_order": 0,
    },
    {
        "key": "gpt-realtime-2.1",
        "transport": "realtime",
        "model": "gpt-realtime-2.1",
        "note": "turn-taking, her agent's own brain; billed by its tokens",
        "instructions": FINANCE_REALTIME_INSTRUCTIONS,
        "max_output_tokens": REALTIME_REPLY_CAP,
        "sort_order": 1,
    },
    {
        "key": "gpt-realtime-2.1-mini",
        "transport": "realtime",
        "model": "gpt-realtime-2.1-mini",
        "note": "turn-taking, cheapest",
        "warning": "cannot use her code mode yet (spike, #272)",
        "instructions": FINANCE_REALTIME_INSTRUCTIONS,
        "max_output_tokens": REALTIME_REPLY_CAP,
        "sort_order": 2,
    },
    {
        "key": "gemini-live",
        "transport": "relay",
        "model": "gemini-3.8-live",
        "note": "turn-taking, her agent's own brain, through our server",
        "instructions": FINANCE_REALTIME_INSTRUCTIONS,
        "max_output_tokens": REALTIME_REPLY_CAP,
        "sort_order": 3,
    },
)
