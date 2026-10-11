"""Files from outside: read in under a cap, served back out by what they are.

An upload's claimed type is the uploader's word and an email attachment's is
anybody's, so the served type comes from the bytes. Only a short list of
types a browser renders harmlessly is shown inline; everything else is a
download. nosniff is the app-wide header, asserted through the app in
tests/api/test_documents_endpoints.py.
"""

from __future__ import annotations

import io

from fastapi import HTTPException, UploadFile
import pytest

from app.core.uploads import read_upload, stored_file

PDF = b"%PDF-1.7\n1 0 obj\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 16
GIF = b"GIF89a" + b"\x00" * 16
WEBP = b"RIFF\x10\x00\x00\x00WEBPVP8 " + b"\x00" * 8
HTML = b"<html><script>alert(document.cookie)</script></html>"
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'


def _upload(data: bytes) -> UploadFile:
    return UploadFile(io.BytesIO(data), filename="x.bin")


class TestReadUpload:
    @pytest.mark.asyncio
    async def test_a_file_under_the_limit_is_read_whole(self) -> None:
        assert await read_upload(_upload(b"x" * 100), limit=100) == b"x" * 100

    @pytest.mark.asyncio
    async def test_a_file_over_the_limit_is_413_before_it_is_read_whole(
        self,
    ) -> None:
        body = b"x" * (8 * 1024 * 1024)
        upload = _upload(body)

        with pytest.raises(HTTPException) as refused:
            await read_upload(upload, limit=1024)

        assert refused.value.status_code == 413
        # Stopped just past the limit, not at the end.
        assert upload.file.tell() < len(body)

    @pytest.mark.asyncio
    async def test_a_known_size_over_the_limit_is_413_before_any_read(
        self,
    ) -> None:
        upload = UploadFile(io.BytesIO(b"x" * 2048), filename="x.bin", size=2048)

        with pytest.raises(HTTPException) as refused:
            await read_upload(upload, limit=1024)

        assert refused.value.status_code == 413
        assert upload.file.tell() == 0


class TestStoredFile:
    @pytest.mark.parametrize(
        ("data", "served"),
        [
            (PDF, "application/pdf"),
            (PNG, "image/png"),
            (JPEG, "image/jpeg"),
            (GIF, "image/gif"),
            (WEBP, "image/webp"),
        ],
    )
    def test_an_allowlisted_type_is_served_inline_as_the_bytes_say(
        self, data: bytes, served: str
    ) -> None:
        # The claim is a lie on purpose: the bytes decide.
        response = stored_file(data, "scan", claimed="text/html")

        assert response.media_type == served
        assert response.headers["content-disposition"].startswith("inline;")

    @pytest.mark.parametrize("data", [HTML, SVG, b"\x00\x01binary"])
    def test_anything_else_is_a_download_the_browser_cannot_render(
        self, data: bytes
    ) -> None:
        response = stored_file(data, "page.html", claimed="text/html")

        assert response.media_type == "application/octet-stream"
        assert response.headers["content-disposition"] == (
            'attachment; filename="page.html"'
        )
        assert response.headers["content-security-policy"] == "sandbox"

    def test_an_image_is_sandboxed_when_opened_on_its_own(self) -> None:
        assert stored_file(PNG, "p").headers["content-security-policy"] == "sandbox"

    def test_a_pdf_is_not_sandboxed_so_the_viewer_still_opens_it(self) -> None:
        """Browsers refuse to start their PDF viewer in a sandboxed
        document; the viewer runs off this origin, so there is nothing for
        the sandbox to contain."""
        assert "content-security-policy" not in stored_file(PDF, "p").headers

    def test_plain_text_claimed_as_text_reads_inline_as_text(self) -> None:
        response = stored_file(
            b"Your claim was processed.", "body", claimed="text/plain"
        )

        assert response.media_type == "text/plain; charset=utf-8"
        assert response.headers["content-disposition"].startswith("inline;")
        assert response.headers["content-security-policy"] == "sandbox"

    def test_html_claimed_as_text_is_still_text(self) -> None:
        """nosniff (on every response, see test_security_headers) plus
        text/plain: the markup is shown, never run."""
        response = stored_file(HTML, "body", claimed="text/plain")

        assert response.media_type == "text/plain; charset=utf-8"

    def test_a_download_is_an_attachment_even_when_it_could_show(self) -> None:
        response = stored_file(PDF, "statement.pdf", download=True)

        assert response.media_type == "application/pdf"
        assert response.headers["content-disposition"] == (
            'attachment; filename="statement.pdf"'
        )

    def test_the_filename_cannot_inject_a_header(self) -> None:
        response = stored_file(HTML, 'a"\r\nSet-Cookie: x=1')

        assert "\n" not in response.headers["content-disposition"]

    def test_extra_headers_ride_along(self) -> None:
        response = stored_file(PNG, "p", headers={"Cache-Control": "no-store"})

        assert response.headers["cache-control"] == "no-store"
