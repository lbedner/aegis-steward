---
name: add-page
description: Use when adding or changing a page in this project's htmx web frontend. Covers the six interaction patterns, the response contract, the render helper, the macro kit, section registration, and the two-render-path test.
---

# Add page

Add a server-rendered page to the htmx web frontend. Pages live under
`app/components/web_frontend/`, are built from six interaction patterns, and
render through one helper that serves both a cold load and an htmx swap from
the same URL and the same template.

There is no client framework. Alpine holds local UI state (a drawer, a
selection count); htmx does every round trip; the server renders the HTML.

## When to use

Use when adding a section, a tab, a dialog, a table, or any control that talks
to the server from the web frontend.

Do NOT use for the Flet dashboard (`app/components/frontend/`), for API
endpoints (use `add-api-endpoint`), or for schema changes (use
`add-model-and-migration`).

## Files that change

- `app/components/web_frontend/nav.py` — `NAV` is the single source for the
  sidebar links AND the section routes. A new top-level section starts here.
- `app/components/web_frontend/routes/<section>/` — the route module. Split per
  section; **500 lines per module**, enforced by
  `tests/test_module_size_budget.py`. Pulse's 1825-line `pages.py` is the
  anti-pattern. The budget is a ratchet: a new module must meet 500 outright,
  and an existing one may shrink but never grow.
- `app/components/web_frontend/main.py` — `create_web_frontend_app()` includes
  each section's router. A route is unreachable until it is registered.
- `templates/pages/<name>.html` — the page. Ends with `{% extends layout %}`.
- `templates/partials/<section>/<name>.html` — a fragment or dialog body.
- `templates/components/macros/` — the kit. Extend a macro; do not write a
  second one.
- `tests/web/test_<name>.py` — both render paths.

## The six interaction patterns

Every screen is built from these, each with exactly one canonical macro.
**Anything that does not fit is a discussion, not a seventh pattern.**

| # | Pattern | Mechanism |
|---|---------|-----------|
| 1 | Form round trip | `hx-post` form → the same fragment back: 200 swaps the changed thing, 422 re-renders with errors |
| 2 | Row action | `hx-post`/`hx-delete` on a row → that row's `outerHTML` back, plus OOB swaps for counters |
| 3 | List re-render | `hx-get` with search/filter/page params → the list fragment back |
| 4 | Dialog | a button `hx-get`s content into the one `<dialog>`, then pattern 1 inside it |
| 5 | Job progress | `hx-ext="sse"` on `/api/v1/jobs/{id}/events`; the terminal frame swaps the result |
| 6 | Feedback | `HX-Trigger` → toast; the global `htmx:responseError` handler → error toast |

### The response contract

Every macro in patterns 1–4 owes five answers:

- **success** — swap the thing that changed, nothing more
- **validation error** — 422 re-rendering the same fragment with `error_banner`
- **server error** — a toast, via the global handler; 4xx/5xx never swap
- **loading** — `hx-indicator` plus the `.htmx-request` CSS
- **empty** — the `empty_state` macro, never a blank region

## Never write an hx attribute by hand

One recipe per intention, in `rendering.py`. A hand-written set drifts, and
`hx-swap` is **inherited from ancestors** — an opener inside a row form that
swaps itself `outerHTML` will borrow that and destroy the dialog target.

| helper | intention |
|---|---|
| `hx_page(url)` | go to this view (swap `#app-content`, push the URL, scroll to top) |
| `hx_replace(url, target, oob=None)` | re-request and replace `target` in place |
| `hx_filter(url, target)` | narrow a list where it stands, without swapping the control being typed into |
| `hx_dialog(url, extra="")` | open this in the one modal |
| `hx_dialog_post(url)` | a dialog's own form posts back into the dialog |

## Rendering

```python
render(request, "pages/thing.html", {...})   # a page, either render path
dialog(request, "partials/x/thing.html", **ctx)  # a dialog body, never a layout
```

`render()` sets `layout` to the app shell on a cold load and to the bare
fragment when htmx asks, so **one URL, one template, two bodies** — and sets
`Vary: HX-Request` so caches know.

Ending a dialog:

- `with_toast(response, text)` — say what happened
- `close_dialog(response)` — shut it, swap nothing
- `dialog_done(path, toast)` — shut it, go somewhere, say what happened
- `where_from(request, fallback)` — **where the reader was standing**. htmx
  sends `HX-Current-URL` with every request; a dialog that hardcodes its
  destination sends people somewhere they did not ask to go.

## The macro kit

Before writing markup, look for the macro. There are already ~85 of them.

- `layout.html` — `card`, `page_header`, `stats_strip`, `stat_tile`, `figures`,
  `dialog`, `dialog_title`, `confirm`, `dropdown`, `menu_item`, `badge`,
  `avatar`, `chip`, `tab_bar`, `chart_panel`, `progress`, `ranked_rows`, `brand`
- `form.html` — `field`, `text_input`, `money_input`, `date_input`, `select`,
  `checkbox`, `textarea`, `search_input`, `picker`, `range_chips`,
  `primary_button`, `submit_button`, `action`, `term_fields`, `control`
- `table.html` — `data_table` (the one table: columns + rows, with `select`,
  `sort`, `scroll`, `flush`, and `row` for a caller-owned row), `table_row`,
  `pager`, `new_mark`
- `feedback.html` — `empty_state`, `error_banner`, `toast_region`, `oob`,
  `sse_job`
- `changes.html`, `imports.html`, `accounts.html`, `chat.html`, `bills.html` —
  domain vocabularies

**Duplication is a bug, not a nice-to-have.** If markup, a selector, an
attribute recipe or a label appears twice, fold it into the kit in the same
session. An agent can only change a thing safely when the thing has one home.

## Rules that have each cost a session

1. **Labels and copy live in one place.** A card title and the menu item that
   edits it read the same row (`ACCOUNT_THINGS`). Two names for one thing is a
   reader wondering whether they are the same thing.
2. **Tailwind classes must be visible to the scanner.** `lg:grid-cols-{{ n }}`
   compiles to nothing. Look the class up in a dict; never build it.
3. **Never bind config or a label at import time** — bind through a function.
4. **A cell that does something must look like it does.** A hover fill is only
   found by a mouse already on it.
5. **The body scrolls, the chrome does not.** A dialog that scrolls as a whole
   takes its own close button off the screen.
6. **`display: flex` on a `<dialog>`** beats the UA rule that hides it when
   closed. Put layout on a wrapper.
7. **Jinja has no list comprehensions**, and template edits can serve stale
   until the process restarts.
8. **Arbitrary Tailwind values need a CSS rebuild** (`make build-static`).

## Registering a section

```python
# nav.py — the sidebar link and the route prefix, together
Section("things", "Things", "/things", "M2.25 ...")   # heroicon path

# routes/things.py
SECTION = section("things")           # a typo is a startup error, not a 404
router = APIRouter()

@router.get(SECTION.path, include_in_schema=False)
async def page(request: Request, ...) -> Response:
    return render(request, "pages/things.html", {...})

# main.py — until this line exists, the route does not
router.include_router(things_router)
```

## Testing

Both render paths, every time. `client` is a cold load (full document), `hx` is
an htmx request (bare fragment).

```python
def test_page_renders(client, ledger):
    page = client.get("/things").text
    one(page, "#things")                 # exactly one, or fail

def test_fragment_has_no_shell(hx):
    none(hx.get("/things").text, "html") # a fragment must not carry the shell
```

Assert with **selectors, never substrings** (`tests/web/dom.py`): `select`,
`one`, `none`, `text`, `card`, `stat`, `table_rows`, `chart_data`, `location`,
`triggers`, `oob`.

- **Never strip OOB in a test.** The counter update is what pattern 2 promises;
  `oob(html)` splits the response into the primary element and its siblings.
- **Assert behaviour, not copy.** A test that hardcodes a label is a second home
  for it — read the label from the constant that defines it.
- **Write the failing test first** and confirm it fails for the right reason.

## Checklist

- [ ] `NAV` entry, if it is a section
- [ ] route module under 500 lines, registered in `main.py`
- [ ] page template ends with `{% extends layout %}`
- [ ] every hx attribute from a `rendering.py` helper
- [ ] every control from the macro kit, or added to it
- [ ] the five response-contract answers
- [ ] both render paths tested, selectors not substrings
- [ ] `make check` green
