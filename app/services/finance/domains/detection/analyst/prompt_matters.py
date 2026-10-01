"""A case's own change types in the assistant's words: opening one, and
what happened on it.

Spliced into ``PROPOSING_CHANGES`` where they always sat, and kept in a
module of its own because that one is at its size budget (#284), as the
plan's entries are (``prompt_planning``).
"""

MATTER_CHANGES = """\
- `matter.create` - payload {"title", optional "kind" (free text: \
medicaid, insurance, dental...), "reference" (the other side's own case \
number), "subject_party_id" (parties() - who it is ABOUT), \
"counterpart_party_id" (parties() - who it is WITH), "opened_on" \
"YYYY-MM-DD", "note"}: open a case when asked to, or when something \
needs a case to belong to and matters() has none. Somebody not yet in \
parties() is a contact.create first. A reference another case already \
carries is that case - use it rather than opening a second. Once the card \
is approved the matter is in matters(); propose what happened on it (a \
visit, a call) as a matter.event then.
- `matter.event` - payload {"matter_id": int, "occurred_at": \
"YYYY-MM-DD", "kind": call/mailed/visit/note, "summary", optional \
"party_id" (parties()), "document_id"}: what happened on a case that \
left NO paper - a phone call, a packet posted, an office visit. The \
rest of a matter's timeline is derived from rows that already exist, so \
this is the only part of the story with nowhere else to live, and being \
told it is the only way it ever gets recorded. "occurred_at" is the day \
it HAPPENED, never today: somebody tells you on Friday about Tuesday's \
call, and filing it under Friday puts the story out of order. Do not \
propose one for something that already has a row - a letter that \
arrived is a document, an ask is an ask, a figure is a fact.
"""
