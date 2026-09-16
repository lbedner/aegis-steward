"""The words the matters pages say.

One file, because copy is the thing most likely to be typed twice and
drift: a status spelled by ``|replace("_", " ")|title`` in one template
and written out in another, a heading that says "asks" where the button
below it says "items". The model's vocabulary and the reader's are not
the same, and this is where the translation lives.

The model says ``party``; nobody says party. It says ``request_item``;
the letter says "what we asked for" and a person says "an ask". Code
keeps its nouns - they are precise, and renaming a table to match a
button is how a schema ends up describing a screen - and every string a
reader sees comes from here.
"""

from __future__ import annotations

# What somebody IS to a case. The role is per matter: Eleanor is the
# facility here and a payee in the ledger.
ROLE_LABELS: dict[str, str] = {
    "subject": "Subject",
    "representative": "Representative",
    "agency": "Agency",
    "facility": "Facility",
    "counsel": "Counsel",
    "other": "Also in it",
}

# Where an ask stands. "Not applicable" is yours and "waived" is theirs,
# and the difference is worth the two extra words.
ITEM_STATUS_LABELS: dict[str, str] = {
    "needed": "Still needed",
    "satisfied": "Satisfied",
    "not_applicable": "Not applicable",
    "waived": "Waived",
}

# The verb that PUTS an ask in a state: generic, one word where it can
# be. The state itself is named above; "Complete" is what you do, and
# "Satisfied" is what it then reads as.
ITEM_VERBS: dict[str, str] = {
    "satisfied": "Complete",
    "not_applicable": "Not applicable",
    "waived": "Waived",
    "needed": "Reopen",
}

REQUEST_STATUS_LABELS: dict[str, str] = {
    "open": "open",
    "satisfied": "satisfied",
    "waived": "waived",
}

# The nouns and the headings, once. A screen that calls one thing three
# names teaches the reader that the three are different things.
WORDS: dict[str, str] = {
    # A person or an organization, in the address book.
    "someone": "someone",
    "add_someone": "Add someone",
    # One demand inside a request.
    "ask": "ask",
    "add_ask": "Add an ask",
    # The verbs on one ask.
    "edit": "Edit",
    "attach": "Attach a document",
    "replace": "Replace the document",
    "remove": "Remove the document",
    "mark_as": "Mark as",
    # One letter's demands, and the deadline that came with them.
    "request": "request",
    "add_request": "Record a request",
    "letter": "The letter",
    # A claim you can stand behind, with its source.
    "fact": "fact",
    "add_fact": "Record a fact",
    # Headings.
    "who": "Who is in it",
    "asked_for": "What they asked for",
    "can_say": "What we can say",
    "paper": "Paper on this matter",
    "add_paper": "Add a document",
}


def word(key: str) -> str:
    """One string the pages say. Missing keys shout in the template
    rather than rendering an empty heading nobody notices."""
    return WORDS[key]


def role_label(role: str) -> str:
    return ROLE_LABELS.get(role, role.replace("_", " ").title())


def item_status(status: str) -> str:
    return ITEM_STATUS_LABELS.get(status, status.replace("_", " ").title())


def item_verb(status: str) -> str:
    return ITEM_VERBS.get(status, item_status(status))
