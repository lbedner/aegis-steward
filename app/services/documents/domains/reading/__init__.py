"""Reading a document's own facts off its pages, without a model.

ST-08's principle is that extraction PROPOSES: nothing read here becomes
true until somebody approves a card. That shapes the whole package. The
readers are deterministic and conservative, they carry the phrase and
the page every finding came from, and they say NOTHING rather than
guess. A blank field sends a person to look at the page; a confidently
wrong execution date on a legal document is the failure the approval
queue exists to prevent.

Pure functions over page text, so they are testable without a database
and reusable by anything else that has to read a page (the letterhead
lookup in the contact milestone reads the same way).
"""

from app.services.documents.domains.reading.changes import (
    EvidencePayload,
    MetadataPayload,
    ReadAsk,
    ReadValue,
    RequestPayload,
    evidence_describe,
    evidence_execute,
    metadata_describe,
    metadata_execute,
    request_describe,
    request_execute,
)
from app.services.documents.domains.reading.findings import Finding, Page
from app.services.documents.domains.reading.metadata import read_document
from app.services.documents.domains.reading.proposals import propose_reading

__all__ = [
    "Finding",
    "EvidencePayload",
    "MetadataPayload",
    "Page",
    "ReadAsk",
    "ReadValue",
    "RequestPayload",
    "metadata_describe",
    "evidence_describe",
    "evidence_execute",
    "metadata_execute",
    "propose_reading",
    "read_document",
    "request_describe",
    "request_execute",
]
