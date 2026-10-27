# Where steward differs from the template

Steward is an Aegis Stack project ported by hand, not by `aegis update`
(backported steward work would come back round). Each port lands cleanly
only where steward's shared files read as the template's do (#505). This
page is the one list of where they still differ, and why.

Measured against aegis-stack main (5bb0c7b3) rendered with steward's
answers (`.copier-answers.yml`), three ways from 850d687, the commit
steward last caught up to, 2026-10-27 (issue 587).

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
- **The macro kit** (`macros/layout.html`, `form.html`, `table.html`):
  steward's is the newer, and the template's later changes are in it.
  Steward's own recipes stand where the template has another: buttons
  through `button_classes` (no `_button_size`), controls through
  `control`, a dropdown with `tone`, `size` and `panel`, `hint_panel` as
  the one tooltip shell (no `popover_panel`, `hover_hint`,
  `info_tooltip`), `select` and `picker` (no `select_field`), and the
  native dialog (no `modal_scrim`). Not taken, with nothing here to use
  them: `or_divider`, a chat card's own dialog sizes.
- **Markdown in `markup.py`**: the markdown, highlighting and message
  rendering the template keeps in `web_frontend/filters.py` live in
  `web_frontend/markup.py`, re-exported by `filters`.
- **Scripts from outside** (`base.html`, `charts.js`): htmx, Alpine and
  Chart.js are served from `static/vendor/`, so the template's CDN
  integrity hashes have nothing to check.
- **No landing page**: `/` opens Overview, so the template's landing,
  `tests/shell.py`'s shell and the site route stay out.
- **The change queue is finance's own**, not `app.services.change_queue`,
  so the system model (`app.core.ontology`) has no change-type nodes.
- **The LLM picker** is steward's, not `app.core.model_picker`.
- **The tool registry** is the template's (`app.core.tools`), but an
  agent's tools resolve through steward's `resolve_tools`, which adds each
  `*_usd` beside its cents; steward's memory tools file a card, so their
  effect is `proposes`.
- **Compose's limits and volumes** (`docker-compose*.yml`): steward's
  memory and swap ceilings, its database on a named volume, Redis's
  password and the dev webserver's room for reload stay; the engine and
  loop pass through as the template's do.
- **Dependencies** (`pyproject.toml`): steward's own, OCR (`pytesseract`),
  web push (`pywebpush`), pydantic-ai's `google` extra and the `docs`
  extra, beside the template's.
- **Steward's own services' parts**: matters and mail are part of
  documents, insurance of finance (`PART_OF`).
- **The AI service's turn and voice** (`ai/service/` beneath the facade,
  `ai/domains/voice/`, the prompt's context blocks, the chat API in
  `api/ai/router.py`): steward's chat runs on them, so they stay steward's.
  The template's later work there (the turn built in `turn.py`, nothing
  live in the prompt, a tool's `ValueError` answered rather than raised,
  voice usage in `llm_usage` alone) lands only as steward's chat takes it.
- **Voice**: the template's later voice work (Gemini's voices and its
  voice library, the pronunciation list, realtime speech, voice profiles
  designed in Overseer) is not taken; steward's voice is its own, as its
  chat is. OpenAI retires `/v1/audio/speech` (`tts-1`) on 2027-01-06; the
  template moved to `gpt-realtime-2.1-mini` over the Realtime API, which
  steward's voice has yet to.
- **Chat's later additions**: code mode's cards, path cards and
  `mentions.js` (ask about a transcript line) serve the template's chat
  surface, so they are not taken. Illiana's shield on the floating button
  is (`static/illiana.png`).
- **Tool results**: the template's system tools hand back models, and
  steward's chat reads dicts, so `with_dollars` reads a model as its JSON
  data. Finance's tools stay dicts. The template's `requests` tool (the
  server's kept requests) is `server_requests` here: `requests` is
  matters'.
- **The favicon** is steward's brand mark, not the Aegis shield.
- **`web_frontend/ranges.py`** stays for the web's own parts (a picked
  range, the remembered one, the longer and purchase windows); the
  vocabulary is the template's `app.core.ranges`, imported.
- **Aegis's own standard is steward's too**: the template skips its layout
  and budget tests outside Aegis's CI (`CONFORMANCE`); steward runs them.
- **Documents' file response**: `stored_file` already sniffs the type and
  sandboxes it, so the template's `app.core.untrusted` is not taken.
- **The AI aggregates** (`ai/aggregates.py`) need spend windows and a
  sentiment model steward's chat does not have, so the AI part answers no
  `aggregate` read yet.
- **The one `AIService`** lives in `app/services/ai/deps.py`, not the
  template's `api/ai/service.py`: a service (its health check) must not
  import a component for it.

## Not yet in line

Left as ratchets, each shrinking as the code it names moves.

- **Services**: every caller outside a service imports it through its
  facade (finance's lazy `service/` package as the template's;
  documents', matters' and insurance's `service.py`), and
  `tests/services/test_service_layout.py` is the template's with no
  facade debt. Left as ratchets there: reads outside `queries` modules
  (`READS_DEBT`), finance's and comms' loose modules (`LOOSE_DEBT`: comms'
  `push.py` and `call.py` are steward's own) and one
  finance job nothing runs (`JOBS_DEBT`).
