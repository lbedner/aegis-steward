# Where steward differs from the template

Steward is an Aegis Stack project ported by hand, not by `aegis update`
(backported steward work would come back round). Each port lands cleanly
only where steward's shared files read as the template's do (#505). This
page is the one list of where they still differ, and why.

Measured against aegis-stack main rendered with steward's answers
(`.copier-answers.yml`), 2026-10-21.

## On purpose

- **Chat** (`static/js/chat.js`, `static/js/voice.js`, `macros/chat.html`,
  `partials/chat/`): steward's chat is the reference; the template's
  follows it, never the other way.
- **`avatar`'s fallbacks** (`macros/layout.html`): the template's
  signature (`name, icon_url, size`), plus `category=` and `glyph=` for a
  payee's category and an account's kind when there is no logo.

## Not yet in line

Each is a slice of #505; a port touching one still needs a hand.

- **Parts and concerns** (`app.core.parts`, `app.core.ownership`,
  `Category`): the template's sidebar groups pages by concern and maps
  containers to pages through `PARTS`; steward's `runtime.py`, nav and
  Overseer pages predate it.
- **Macros and styles** (`macros/layout.html`, `macros/form.html`,
  `macros/table.html`, `static/input.css`, `static/js/app.js`): both
  sides changed them. `menu_item` and `dropdown` differ (steward's
  dropdown carries `tone`, `size`, `panel`; the template's closes from
  Alpine).
- **Template features steward lacks**: the run log (`app.core.run_log`,
  Overseer's runs), `source_index` in core, and the rest new since
  steward's last update; each is a choice to adopt, not drift.
