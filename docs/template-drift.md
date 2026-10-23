# Where steward differs from the template

Steward is an Aegis Stack project ported by hand, not by `aegis update`
(backported steward work would come back round). Each port lands cleanly
only where steward's shared files read as the template's do (#505). This
page is the one list of where they still differ, and why.

Measured against aegis-stack main (850d687) rendered with steward's
answers (`.copier-answers.yml`), three ways from 44a3670, the commit
steward last caught up to, 2026-10-22.

## On purpose

- **Chat** (`static/js/chat.js`, `static/js/voice.js`, `macros/chat.html`,
  `partials/chat/`): steward's chat is the reference; the template's
  follows it, never the other way.
- **`avatar`'s fallbacks** (`macros/layout.html`): the template's
  signature (`name, icon_url, size`), plus `category=` and `glyph=` for a
  payee's category and an account's kind when there is no logo.
- **The look**: steward's surfaces are `raised` and `floating`, lit by its
  finish (matte, lustre) and held there by `tests/web/test_theme.py`; a
  page taken from the template wears them in place of `hover-card` and a
  spelled-out card. The template's theme files (`web_frontend/themes/`,
  `WEB_THEME`) are not taken.
- **No landing page**: `/` opens Overview, so the template's landing,
  `tests/shell.py`'s shell and the site route stay out.
- **The change queue is finance's own**, not `app.services.change_queue`,
  so the system model (`app.core.ontology`) has no change-type nodes.
- **The LLM picker** is steward's, not `app.core.model_picker`.
- **The tool registry** is the template's (`app.core.tools`), but an
  agent's tools resolve through steward's `resolve_tools`, which adds each
  `*_usd` beside its cents; steward's memory tools file a card, so their
  effect is `proposes`.
- **Steward's own services' parts**: matters and mail are part of
  documents, insurance of finance (`PART_OF`).
- **The AI service's turn and voice** (`ai/service/` beneath the facade,
  `ai/domains/voice/`, the prompt's context blocks, the chat API in
  `api/ai/router.py`): steward's chat runs on them, so they stay steward's.
  The template's later work there (the turn built in `turn.py`, nothing
  live in the prompt, a tool's `ValueError` answered rather than raised,
  voice usage in `llm_usage` alone) lands only as steward's chat takes it.
- **The one `AIService`** lives in `app/services/ai/deps.py`, not the
  template's `api/ai/service.py`: a service (its health check) must not
  import a component for it.

## Not yet in line

Each is a slice of #505; a port touching one still needs a hand.

- **Services**: every caller outside a service imports it through its
  facade (finance's lazy `service/` package as the template's;
  documents', matters' and insurance's `service.py`), and
  `tests/services/test_service_layout.py` is the template's with no
  facade debt. Left as ratchets there: reads outside `queries` modules
  (`READS_DEBT`), finance's and comms' loose modules (`LOOSE_DEBT`: comms'
  `push.py` and `call.py` are steward's own) and one
  finance job nothing runs (`JOBS_DEBT`).
- **CLI**: `patterns check/review/touches`, `ontology export`, and the
  locale strings they bring.
- **Config and boot**: the webserver engine and loop as Overseer
  settings (`configurable`, `loops`, `boot.apply_before_serving`),
  `scripts/dev_tasks.py`, compose, `pyproject.toml`.
- **Macros, styles and scripts** beyond what the Overseer pages need
  (`macros/layout.html`, `form.html`, `table.html`, `base.html`,
  `input.css`, `charts.js`, `theme.js`): `menu_item` and `dropdown` differ
  (steward's dropdown carries `tone`, `size`, `panel`).
