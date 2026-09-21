"""Mail that arrived as a file, read the way the shelf reads paper.

No connection, no OAuth, no polling: a Google Takeout ``.mbox`` or a
saved ``.eml`` is uploaded like any other document, and the pipeline
does the half that happens after the messages exist - the sender is
matched to a contact by address, and every attachment becomes a
document on the shelf, which already names, dates and files it.

Distinct from ``comms``, which SENDS. Steward-shaped only in that
steward has the shelf; the reader belongs upstream once it has proved
itself here (backport ledger).
"""
