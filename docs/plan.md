# Aegis Steward — build plan

htmx frontend for the aegis-stack finance service. Every `P*.n` line is a
ticket.

## Decisions (locked unless revisited on purpose)

1. **Interface is Pulse's app shell, not Overseer's card-and-modal dashboard.**
   Persistent left sidebar, one `#app-content` main, one full page per
   section. No marketing landing, pricing, or onboarding. `/` redirects to
   `/overview`.
2. **No auth to start.** Finance is single-user when auth is absent (owner id
   `None`). Add later with `aegis add-service auth`; nothing in the plan
   depends on it. Consequences: no CSRF surface, no login pages, no page
   gate.
3. **htmx-first.** The server returns the HTML of the new state. Alpine is
   for local UI state only (open/closed, hover). No client-side data models,
   no JSON fetches from templates.
4. **Two JS islands, no more:** Chart.js (net worth, cash flow, projection)
   and the chat drawer (NDJSON stream). Both lifted from Pulse.
5. **One route per view.** The handler checks the `HX-Request` header and
   extends either `layouts/app_shell.html` or `layouts/fragment.html`. Never
   two URLs for one view.
6. **htmx routes never reimplement the API.** They call the API handler
   function or the `FinanceService` facade in-process, the way the
   framework's login route calls `api_login`. No HTTP to self. The JSON API
   stays untouched for CLI, Flet, and tests.
7. **Fragments carry no inline scripts.** Kills the script re-exec hack in
   `app.js` and keeps a CSP possible later.
8. **Native `<dialog>` for modals.** Focus trap, escape, backdrop for free.
9. **Validation errors are a 422 that re-renders the form.** Enabled by
   `responseHandling` in the existing htmx-config meta tag, not an
   extension.
10. **Toasts via `HX-Trigger` response header**, not sessionStorage + reload.
11. **Cross-cutting account filter is a URL query param** (`account_ids`),
    carried with `hx-include`. No client store.
12. **Macros split by concern from day one**: `components/macros/form.html`,
    `table.html`, `layout.html`, `feedback.html`, `money.html`. Macros live
    only in those files. One implementation per control; a v2 replaces v1,
    never sits beside it.
13. **The agent contract ships as a skill, not a docs tree.** One
    `add-page` skill in `.claude/skills/`, written after Phase 3 from what
    converged, not before.
14. **Out-of-band swaps have one shape on both sides.** Routes emit OOB
    siblings only through an `oob(target_id)` macro; the test kit's
    `oob(html)` splits a response into the primary element and its OOB
    siblings, so `one` selects against the primary and the counters get
    their own assertions. Never strip OOB in tests: the counter update is
    what pattern 2 promises. Lands with the first OOB response (P3.3).
15. **Module size budget applies**: 500 lines per logic module. Routes split
    per section (`routes/finance/accounts.py`, ...). Pulse's 1825-line
    `pages.py` is the anti-pattern.

## The six interaction patterns

Every screen is built from these, each with exactly one canonical macro or
example. Anything that does not fit is a discussion, not a seventh pattern.

| # | Pattern | Mechanism | First appears |
|---|---------|-----------|---------------|
| 1 | Form round trip | `hx-post` form → same form fragment back (200 on success with swap of the changed thing, 422 with errors) | P3.5 add account |
| 2 | Row action | `hx-post`/`hx-delete` on a row → row `outerHTML` back, OOB swaps for counters | P3.3 categorize |
| 3 | List re-render | `hx-get` with search/filter/page params → list fragment back | P3.2 register |
| 4 | Dialog | button `hx-get`s dialog content into a `<dialog>` target, then pattern 1 inside | P3.6 reconcile |
| 5 | Job progress | `hx-ext="sse"` on `/api/v1/jobs/{id}/events`, terminal frame swaps the result | P3.9 import |
| 6 | Feedback | `HX-Trigger` → toast; global `htmx:responseError` → error toast | P1.6 |

Response contract for every macro in patterns 1–4: success, validation error
(422), server error (toast), loading (`hx-indicator` + `.htmx-request` CSS),
empty state (`empty_state` macro).

## Phase 0 — generate and first commit

- **P0.1** Generate. The empty dir must go first (`--force` would delete it
  anyway):
  ```bash
  cd ~/Workspace/house_bedner && rmdir aegis-steward
  aegis init aegis-steward --blueprint finance --no-interactive
  cd aegis-steward && aegis add htmx && aegis add-service comms
  ```
  Expected stack: redis, worker, scheduler[sqlite], database[sqlite], htmx;
  services ai[sqlite,pydantic-ai,ollama], finance, comms. Check `.env` for
  `ollama_mode` (finance-app uses `host`).
- **P0.2** `make check` green, `make serve` up, landing renders at `/`,
  `/api/v1/finance/health` answers.
- **P0.3** `.gitignore` covers `app/components/web_frontend/static/dist/`
  (done by `aegis add htmx`; Pulse committed ~350 stale builds).
- **P0.4** First commit with htmx + comms added. Open tickets from the `P*`
  lines.

## Phase 1 — shell and foundation (no finance yet)

- **P1.1** `layouts/app_shell.html` lifted from Pulse minus the
  `appShellGate` user check, the Plausible block, and the chat drawer
  (returns in P8). Keep: sidebar include, mobile scrim, `#app-content`,
  snackbar outside the scroll container.
- **P1.2** `layouts/fragment.html`: renders `{% block app_content %}` only.
- **P1.3** `components/sidebar.html` with `_nav_item` moved into
  `macros/layout.html` as `nav_item(key, label, path, icon)`. Nav comes from
  one Python list `NAV` in `routes/finance/__init__.py`; the sidebar loops
  it. No second copy of the section list anywhere.
- **P1.4** `render(request, template, ctx)` helper in `web_frontend/main.py`:
  sets `ctx["layout"]` to app shell or fragment from the `HX-Request`
  header. Templates open with
  `{% extends layout %}`.
- **P1.5** htmx config meta: keep `historyCacheSize: 0`, add
  `responseHandling` so 422 swaps and 5xx does not. Add the SSE extension
  script tag next to htmx.
- **P1.6** Feedback: `macros/feedback.html` with `toast()` container that
  listens for the `toast` event from `HX-Trigger`, `empty_state(title,
  hint)`, `error_banner(errors)`. One global `htmx:responseError` listener
  in `app.js`. Delete the sessionStorage snackbar contract and the script
  re-exec hook.
- **P1.7** Jinja filters in `main.py`: `money(cents, currency)`, `short_date`,
  `pct`. Templates never format numbers by hand.
- **P1.8** Routes package `routes/finance/` with one module per section,
  registered in `create_web_frontend_app()`. `/` redirects to `/overview`.
  Delete the landing templates and `components/landing/`.
- **P1.9** Tailwind `content` globs cover the new `layouts/` and `macros/`
  dirs. `make build-static` proves classes are emitted.
- **P1.10** Test helper `tests/web/conftest.py`: `hx(client)` that sends
  `HX-Request: true`; assertions parse the fragment with a tiny selector
  helper instead of raw substring checks.

Done when: every nav item renders an empty page full-load and as a fragment,
tests cover both paths, `make check` green.

## Phase 2 — Overview (first real screen)

Route calls the composite `overview` API handler in-process; one DB session.

- **P2.1** `macros/layout.html`: `card(title)`, `stat_tile(label, value,
  delta)`, `section_header`.
- **P2.2** `macros/table.html`: `data_table(columns, rows, row_url=None,
  sort=None, empty=None)`. The only table macro in the app. Used here for
  recent transactions, top payees, uncategorized preview.
- **P2.3** `chart_panel` lifted from Pulse, trimmed to one control contract
  (Pulse has four). Chart.js loaded only on pages that block-include it.
  Net worth line, spending donut (15-slice cap from Flet constants).
- **P2.4** Account filter: `account_filter(accounts, selected)` macro in
  `macros/form.html`; a `<details>` multi-select posting `account_ids` via
  `hx-get` to the current section. Server-side only.
- **P2.5** Pending-changes banner linking to Review (count comes from the
  same context).
- **P2.6** Donut slice click → `hx-get` spending transactions into a dialog
  (pattern 4, first use).

## Phase 3 — Accounts (largest tab; births most macros)

Layout: accounts list left (grouped Banking / Credit Cards / Investments /
Property / Loans & Debt / Other, group and grand totals), register right.

- **P3.1** Accounts list fragment + account header (identity, balance,
  Manage menu → `dropdown()` macro in `macros/layout.html`).
- **P3.2** Register: `data_table` with search (`hx-trigger="keyup changed
  delay:300ms"`), category / merchant / tag / date filters, pagination,
  transfers toggle. Investment accounts add holdings and trades tables.
- **P3.3** Row actions: categorize inline (`select` posts on change, row
  swaps back, OOB uncategorized count), tag add/remove, delete. First
  `macros/form.html` controls: `field()`, `select()`, `checkbox()`,
  `money_input()`, `date_input()`. All native inputs with `name`. Births
  the OOB contract (decision 14): `oob()` macro and `tests/web/dom.oob()`.
- **P3.4** Bulk actions: checkbox column + action bar, assign payee with
  create-new, "also apply to N similar" offer.
- **P3.5** Add account dialog (pattern 1 inside pattern 4). `dialog()` macro
  in `macros/layout.html` wraps native `<dialog>`.
- **P3.6** Manage: reconcile (preview then commit, two posts to the same
  endpoint), rename/edit, delete with `confirm()` macro (a dialog variant).
- **P3.7** Property details + paste-a-valuation-series (bulk endpoint).
- **P3.8** Secured-by lien picker.
- **P3.9** Import: file input → preview fragment → commit with
  `background=true` → `sse_job(job_id)` macro (pattern 5) → summary
  fragment. Investment import (Optum profile) reuses the same flow.
- **P3.10** Splits editor dialog; declare-recurring preview then commit.

Done when: every Flet register action has an htmx equivalent, and the macro
set has not grown a second table, form field, or dialog implementation.

## Phase 4 — Bills & Income, Projected

- **P4.1** Streams table (columns from Flet: name, category, account, amount,
  cadence, next due, health), row actions pause/resume/mute/unmute/confirm/
  delete, rescan button.
- **P4.2** Add/edit stream dialog; categorize; match-a-payment dialog using
  review-queue + attach.
- **P4.3** Projected: projection chart (Chart.js island #1 reused) with
  overdue markers, upcoming list under it.

## Phase 5 — Budget

- **P5.1** Month pager + stats strip; cell click → stat-details dialog.
- **P5.2** Lines sub-tab grouped commitments / flexible / one-time; upsert
  and delete lines inline (pattern 2).
- **P5.3** Suggestions: dismiss/restore, accept-as-line, natural-language
  goal parse (form round trip).
- **P5.4** Goals: cards, contribute, pause, edit dialog with target preview
  (`hx-get` on input change).
- **P5.5** Envelopes: credit/spend, create/edit/delete.

## Phase 6 — Review

Sub-tabs as sibling routes, not nested tab state.

- **P6.1** Approvals: pending change cards, approve/reject single and batch.
- **P6.2** Uncategorized: list + inline categorize (reuses P3.3) +
  auto-categorize preview.
- **P6.3** No payee: list + assign merchant / payee group (reuses P3.4).
- **P6.4** Attention: insights list with dismiss, plus a "write today's
  note" button calling the analyst run endpoint with `background=true`
  through `sse_job`. Flet never exposed this.

## Phase 7 — Settings

- **P7.1** Connections: cards, disconnect, Plaid hosted-link and SnapTrade
  connect flows (two-step post). Gated by `settings.FINANCE_PLAID` /
  `FINANCE_SNAPTRADE` and credential presence.
- **P7.2** Categories table. **P7.3** Payees table with usage.
- **P7.4** Comms: no finance use yet. Leave the service installed; first
  candidate is a bill-due email from the scheduler. Separate ticket, not
  part of the UI port.

## Phase 8 — Chat

- **P8.1** `chat_drawer` lifted from Pulse, mounted in the app shell,
  `agent_slug="finance-assistant"`, `surface="finance"`, standalone user
  id. NDJSON stream stays JS (island #2). Inline approval cards for pending
  changes call the same routes as P6.1.

## Phase 9 — encode and backport

- **P9.1** Write the `add-page` skill for generated projects from what
  converged in Phases 1–3: the six patterns, the response contract, the
  render helper, the macro files, the test helper.
- **P9.2** Backport ledger (`docs/aegis-stack-backport.md`, same idea as
  Pulse's): app shell + fragment layouts, `render()`, `responseHandling`
  config, `macros/{form,table,layout,feedback}.html`, the test helper, the
  skill. `macros/money.html` and all finance routes stay in steward.
- **P9.3** Framework gap: a service cannot contribute htmx pages today.
  Propose a `pages` wiring entry parallel to `dashboard_modals`, informed by
  what steward's `routes/finance/` needed.
- **P9.4** Upstream bugs found during the survey, file against aegis-stack:
  `GET /finance/categories/options`, `POST /finance/categories`, and the
  investment import preview skip the owner dependency and are open in auth
  builds; the `finance_import` copier flag gates nothing;
  `tests/services/test_service_layout.py` fails on freshly generated ai and
  finance services (reads outside queries modules), so `make check` is red
  out of the box; the framework's
  `select_field` macro is Alpine-shaped and cannot post as a form field.

## Test policy

- Every route: one full-load test and one `HX-Request` test asserting the
  fragment and its key values. Pattern 1 routes add a 422 test.
- No Playwright until a bug appears that fragment tests could not catch.
  The two JS islands are the only candidates.
- Accessibility floor, checked in review not tooling: real `<button>` for
  actions, `<label for>` on every field, `<dialog>` for modals, tables are
  `<table>`.

## Port surface reference

Backend contract: ~130 routes under `/api/v1/finance`, facade
`app/services/finance/service/`, composite `overview` handler in
`app/components/backend/api/finance/overview.py`, jobs SSE at
`/api/v1/jobs/{id}/events` (self-closing, no auth). Flet screen inventory
that this replaces: Overview · Accounts · Bills & Income · Projected ·
Budget · Review (Approvals, Uncategorized, No payee, Attention) · Chat ·
Settings (Connections, Categories, Payees).
