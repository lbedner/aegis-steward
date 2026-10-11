"""Constants the finance sub-routers share, and the upload size gate."""

from fastapi import UploadFile

from app.core import uploads
from app.core.config import settings

_NOT_FOUND = "Account not found"

_INVESTMENT_IMPORT_PROFILES = ("optum",)


async def read_upload(file: UploadFile) -> bytes:
    """An imported file's bytes, refused past the cap every import shares."""
    return await uploads.read_upload(file, settings.IMPORT_MAX_BYTES)
