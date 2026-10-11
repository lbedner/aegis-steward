"""Files from outside, both ways: read in under a cap, served back out by
what the bytes are.

Every upload route reads through ``read_upload`` and every route that hands
stored bytes back answers with ``stored_file``. An upload's claimed type is
the uploader's word, and a mail attachment's is anybody's: served inline
under that claim, an HTML or SVG file is a page on this origin.
"""

from __future__ import annotations

from fastapi import HTTPException, UploadFile, status
from starlette.responses import Response

from app.core.config import settings
from app.core.formatting import safe_filename

# What a browser renders harmlessly, by the bytes it starts with.
_SIGNATURES = (
    (b"%PDF-", "application/pdf"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)
_TEXT = "text/plain; charset=utf-8"


async def read_upload(file: UploadFile, limit: int | None = None) -> bytes:
    """The upload's bytes, a 413 past ``limit`` (``UPLOAD_MAX_BYTES`` when
    unset): before a byte is read when the size is known, else one byte
    past the cap, never the whole file held first and measured after."""
    cap = settings.UPLOAD_MAX_BYTES if limit is None else limit
    if (file.size or 0) <= cap and len(data := await file.read(cap + 1)) <= cap:
        return data
    raise HTTPException(
        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
        detail=f"File too large (max {cap / 2**20:g} MB).",
    )


def _sniff(data: bytes, claimed: str | None) -> str | None:
    for magic, media_type in _SIGNATURES:
        if data.startswith(magic):
            return media_type
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    # Text has no signature; trusted only as plain text, which nosniff
    # (security_headers, on every response) keeps from running as markup.
    if (claimed or "").startswith("text/plain"):
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            return None
        return _TEXT
    return None


def stored_file(
    data: bytes,
    filename: str,
    *,
    claimed: str | None = None,
    download: bool = False,
    headers: dict[str, str] | None = None,
) -> Response:
    """Stored bytes as a response: an allowlisted type inline (an attachment
    when ``download``), anything else an ``application/octet-stream``
    download. The browser may not guess the type, and a document opened on
    its own is sandboxed - except a PDF, which browsers will not open in
    their viewer under a sandbox, and whose viewer runs off this origin."""
    media_type = _sniff(data, claimed)
    disposition = "inline" if media_type and not download else "attachment"
    served = {
        **(headers or {}),
        "Content-Disposition": f'{disposition}; filename="{safe_filename(filename)}"',
    }
    if media_type != "application/pdf":
        served["Content-Security-Policy"] = "sandbox"
    return Response(
        data, media_type=media_type or "application/octet-stream", headers=served
    )
