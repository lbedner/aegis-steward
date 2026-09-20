# Handoff — aegis-steward, 2026-09-19

Untracked file. Delete it when it stops being true. (It replaces the
2026-09-18 one, which shipped almost entirely.)

Repo is on `main`, clean apart from this file. Suite green at **5284
passed, 11 skipped**. One PR open: **#206** (the import worker's size
and the narration that found it - see 2). The stack runs in Docker on
the user's Mac; `worker-system` is a real arq worker, does the reading
AND the importing, and now has 1G / 1.0 CPU to do it in.

---

## 1. The decision waiting on the user

**Auth, and the `0` sentinel that blocks its migration.**

`aegis add-service "auth[rbac,oauth]"` now works (the bracket flag was
silently dropped until aegis-stack#1215, merged today). Adding it brings
28 paths, four migrations, and Google sign-in via Authlib against
Google's OIDC discovery.

Two consequences, both dry-run verified in a throwaway clone, neither
reversible by accident:

1. **`AUTH_ENABLED` flips to `True`** — every surface demands a login
   unless `.env` says otherwise in the same commit.
2. **`036_finance_auth_link` adds 26 FKs to `user.id`, all ON DELETE
   CASCADE** — so deleting the user deletes the ledger (18,619
   transactions). Fine for NULL-owned rows, which is nearly everything.

   **Except `finance_insight`: 154 rows, every one owned by user `0`.**
   That is the sentinel `rules.py:179` describes - insights are NOT-NULL
   owner, so a standalone install stores them under `0`.

**CORRECTED TWICE, 2026-09-19. Both earlier readings were wrong; this
one is measured, not reasoned.** Generated a finance project on
aegis-stack main, migrated it, seeded a `finance_insight` row owned by
`0`, then ran `add-service auth`. It SUCCEEDS, and
`PRAGMA foreign_key_check` is clean:

```
002_auth.py              fk=4   sentinel=False
003_finance_auth_link.py fk=0   sentinel=True
```

There is no ordering problem and there are no 26 CASCADE FKs, because
on the add path **the finance owner FKs are never created at all**.
`003_finance_auth_link` is the sentinel INSERT and nothing else.

So: **adding auth is not blocked.** The 154 insight rows are not at
risk, nothing needs remapping or deleting, and the "deleting the user
deletes the ledger" worry does not apply on this path - the cascades do
not exist. What IS still true: `AUTH_ENABLED` flips to `True`, so the
`.env` has to land in the same commit.

The real defect is drift, filed as **aegis-stack#1217**: a project
generated as `finance + auth` has those FKs inline in its finance
revision (163 `user.id` refs in `002_finance.py`); one that adds auth
later has none, forever, and `aegis update` will not close it because
the revision that would have carried them is already applied.
**aegis-stack#1216** adds the test that found this - the existing one
only ever added auth to an EMPTY database, which is why none of it
surfaced - plus an assertion that the sentinel is written before any FK
in the same revision, which SQLite cannot show (it rebuilds the table
and does not re-validate copied rows) and Postgres would.

Two things NOT proven, so do not repeat them as fact: steward is on
template **0.11.0** and the test ran against **main**; and Postgres is
untested, where alembic can render `ADD CONSTRAINT` and autogenerate
may well emit the keys on the add path.

---

## 2. What shipped today

All merged to `main`.

| PR | What |
|---|---|
| #192 | One home: a routing number is not a way to reach anybody |
| #193 | ST-10 the matter timeline, matter_event, the colour rule |
| #194 | ST-09 the register speaks where nothing proves the figure |
| #195 | ST-09 a deadline nags before it is late |
| #196 | Paper names itself; every card shows the page it read |
| #197 | Paper that lands files itself, and says how it knew |
| #198 | mktemp -> NamedTemporaryFile in the TTS path |
| #206 | An import says what it is doing, and has room to do it (OPEN) |

**#206**, later the same day: an 18,618-row re-import loaded for a
minute and stopped. Not a hang - `worker-system` was SIGKILLed (137)
twelve seconds in. It was sized as the maintenance worker (256M, 0.25
CPU) and then handed the two heaviest jobs in the app,
`finance_import_task` and `extract_document_task`; it idles near 110
MiB before the finance service is even imported. `restart:
unless-stopped` plus `SYSTEM_WORKER_MAX_TRIES=5` made it a retry loop,
which is what the spinner was waiting on. Now 1G / 1.0 CPU.

The ingest logged NOTHING start to finish, so a killed job and a slow
one were indistinguishable from outside. Five stages now:
`finance.import.started` / `planned` / `progress` (every 2000 rows) /
`reconciling` / `finished`, all with elapsed. A run with a start line
and no finish line is a run whose process died.

That narration pushed `imports.py` past its BUDGET entry, which has
only ever gone down, so the module split along the line its docstring
already claimed: `plan_transactions` + `_resolve_account_id` to
`importers/plan.py`, and the shapes every stage shares (`PlannedRow`,
`ImportPlan`, the account-kind rules, `_is_posted`, the skip reasons)
to `importers/base.py` beside `ParsedTransaction`. 1226 -> 765;
callers unchanged via re-export. NOT fixed: nothing streams -
`plan.existing_by_id` still holds every existing transaction while the
planned rows and batch rows pile up in the session. Headroom, not a fix.

Upstream: **aegis-stack#1215 merged** - `auth[oauth]` named no answer,
so `add-service` accepted the flag and dropped it. One line
(`answer_key=AnswerKeys.AUTH_OAUTH`), tests written against the SHIPPED
spec because the test file's fake carried the same omission.

### The reading pipeline, end to end

A document dropped in now: is read on arrival (arq), names itself, says
who sent it, which case and which account it is about, states its
balance where it can, keeps the filename it arrived with, and shows the
page behind every claim. **No model anywhere in that** - the
organization comes from three directories the app already keeps
(contacts, institutions, payees), the heading and date come off the
front page by pattern, and the pages were parsed on arrival.

Rules that were each written by a document that defeated the last:

- The name has to BE the line, not appear in it ("Chase" was read off
  "Purchases +$0.00" and offered as a contact, twice).
- An organization alone is not a name (four Delta Dental documents came
  out with one title); neither is its kind (two came out "... form").
  The heading the kind was read from is what tells them apart, minus any
  identifier ("Application ID: 1903014447" is not a name).
- A heading must BE the kind rather than mention it ("Did you know this
  statement is available electronically" filed a claim statement
  correctly by luck).
- An account mask needs a label ("ending 3639") OR eight-plus bare
  digits ending in it - a real Chase statement prints fifteen with no
  label, and four digits alone is an amount.
- A balance line needs money with a sign or cents, or "New balance as of
  09/07/26" makes the balance nine dollars.
- A figure prints against an ask only where its PAPER is filed - the
  pension figure appeared under the Social Security ask otherwise.

---

## 3. Traps. The 2026-09-18 list still holds; these are new.

### 3.1 Two databases in web tests (not in the app)

`async_db_session` is a per-test transaction that is rolled back;
`app_owned_engine` is a file-backed SQLite standing in for
`AsyncSessionLocal`. Routes reached through the `get_async_db` DEPENDENCY
(finance, review, overview) read the first; routes that call
`get_async_session()` themselves (matters, documents, contacts) read the
second. They cannot be one: the per-test session is inside a transaction
and app-owned sessions commit.

Consequence: data written through one is invisible to the other. It cost
three detours today - an account fixture, the overview deadline test,
and `/documents/1` 404ing in a test while working in the app. Seed
finance data app-side (`tests/web/matters.account_held_by`).

### 3.2 Every job renders ONE terminal frame

`partials/jobs/status.html` was written for the import: "Import
complete", the import's counts, and a Done button that navigates to
Accounts. Attaching the SSE follower to "Read again" meant a finished
READ said that, in the document dialog, and took the reader elsewhere.
It branches on the job now - check it when wiring a follower to anything
new.

### 3.3 Reading proposes; both doors must do it

The worker's job read AND proposed; the API's inline extract only read.
Three documents were read on the real shelf with nothing to show for it.
`read_and_propose()` is that second half, in one place.

### 3.4 `aegis add-service` needs the bracket syntax

`add-service auth -y` takes DEFAULTS (basic, no rbac, no oauth) without
saying so. Ask for what you want: `add-service "auth[rbac,oauth]"`.
Driving copier directly is worse - it ignores the subdirectory setup and
generates a nested project folder, and `copier update` silently aims at
the newest template (0.12.1 while this project is on 0.11.0).

### 3.5 Dev-mode rendering reads the checkout's working tree

`copier_manager`: a git-repo template root renders from HEAD of that
checkout; installed-via-uvx renders from the GitHub tag matching the CLI
version. So running `aegis` from `~/Workspace/house_bedner/aegis-stack`
uses whatever is committed there - clean tree only.

---

## 4. Open work

**Milestone "The mail builds the matter" (#17)** - seven tickets, written
today, the whole point of the next stretch:

- #199 MI-01 mailbox connected (needs the auth decision above first)
- #200 MI-02 fetched once, on a cursor
- #201 MI-03 only the mail that could matter
- #202 MI-04 attachment becomes paper (nearly free - the shelf already
  does everything once it has a file)
- #203 MI-05 the message itself is a letter; identity gains email
  addresses, the strongest identifier yet
- #204 MI-06 what came in, in one place
- #205 MI-07 she reads the day's arrivals TOGETHER and proposes the
  joins - the actual point of the milestone

**Steward: matters** - #144 (ST-08: only the re-extraction no-op left;
Kreuzberg evaluated and closed, aegis-stack#569), #147 cadence, #148 the
tracker.

**Recommended next:** the auth decision, then MI-01. Everything else in
the mail milestone is downstream of it.

---

## 5. Loose ends the user has not decided

- **A plaintext account number in a stored `conversation_message`.**
  Offered many times, never actioned. Do not print it; offer to delete
  the row.
- **Four duplicated reach facts in Illiana's memory** (Leonard's address
  and phone, Marisa's phone and address) that now also live on contact
  rows. Ask her to forget them.
- **Accounts have no last fours** except the 401(k), the two IRAs, and
  now Chase (`3639`, read off page one of its own statement and stored
  through `set_number`, which derives the mask). HVCU's is unknown; with
  it, HVCU statements would file themselves and propose their balance.
- **The county's "balance as of 1 August"** is still unproven: the
  register says $407.99 for James's HVCU checking and no statement on
  the shelf covers that date. The sheet says so plainly, which is the
  honest state.
- **`/matters/{id}` lands on The case**, not the timeline. ST-10 says it
  should open on the timeline; the user said leave it.
- **`auth_service_parser` vs the spec-driven path** are two
  implementations of one question upstream - which is how one of them
  went silent. Worth a ticket.

---

## 6. House style

Unchanged from yesterday, and it earned its keep today:

- **TDD, always.** Three real bugs were caught by the test written
  first: the substring match, the bare account number, the money pattern.
- **Duplication is a bug**, fixed in the same session. Today:
  `findings.flat`, `findings.mostly`, `facts.said_value`, `LINK`,
  `whose_field`, `RequestService.items_of`, `evidence.satisfied_by_many`,
  `DocumentService.get_many`, `tests/web/matters.py`.
- **Comments say WHY, with the incident and its date.** The codebase
  records what went wrong and when; match that voice.
- `uv run poe check` before considering anything done. Do not pipe it.
- Fresh branch per ticket, one commit per PR, no Claude attribution.
- The user is terse. Lead with the answer. When he corrects a fact -
  "that is my account, not his" - the correction is the important part
  of the exchange, not the code.
