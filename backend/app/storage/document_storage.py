"""
DocumentStorage — where uploaded document bytes actually live.

An abstraction so agents never touch the filesystem (or, later, S3)
directly — DocumentVerificationAgent only ever calls `storage.read(ref)`
with the opaque reference stored on DocumentMetadata.storage_reference.
Swapping local disk for S3/object storage later means writing one new
class here; nothing above this layer changes.

Also home to upload validation (allowed types, size limit, magic-byte
sniffing) since it's the boundary where untrusted bytes first enter the
system — the same place a storage backend would need to enforce limits
anyway.
"""

from __future__ import annotations

import os
import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional
from uuid import uuid4

from app.ai.schemas.ai_schemas import MediaType
from app.domain.errors import DocumentTooLargeError, EmptyDocumentError, UnsupportedDocumentTypeError

MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB

# Normalises client-reported content types to our canonical MediaType.
# Some browsers/tools send "image/jpg" instead of the standard "image/jpeg".
_CONTENT_TYPE_ALIASES = {
    "image/jpg": MediaType.JPEG,
    "image/jpeg": MediaType.JPEG,
    "image/png": MediaType.PNG,
    "application/pdf": MediaType.PDF,
}

_EXTENSION_BY_MEDIA_TYPE = {
    MediaType.JPEG: ".jpg",
    MediaType.PNG: ".png",
    MediaType.PDF: ".pdf",
}

# Magic-byte signatures — a cheap, real check that the content actually is
# what its declared content type claims, not just trusting the client.
_SIGNATURES = {
    MediaType.PDF: (b"%PDF",),
    MediaType.JPEG: (b"\xff\xd8\xff",),
    MediaType.PNG: (b"\x89PNG\r\n\x1a\n",),
}


def validate_upload(
    *,
    filename: str,
    content_type: Optional[str],
    content: bytes,
    max_bytes: int = MAX_UPLOAD_BYTES,
) -> MediaType:
    """
    Validate an uploaded file and return its canonical MediaType.

    Raises (all DocumentError subclasses — recoverable, member can retry):
        EmptyDocumentError: no bytes at all
        UnsupportedDocumentTypeError: content type not in the allowlist, or
            the bytes don't match the claimed type's magic-byte signature
        DocumentTooLargeError: exceeds max_bytes
    """
    if not content:
        raise EmptyDocumentError(filename)

    if len(content) > max_bytes:
        raise DocumentTooLargeError(filename, len(content), max_bytes)

    media_type = _CONTENT_TYPE_ALIASES.get((content_type or "").lower())
    if media_type is None:
        raise UnsupportedDocumentTypeError(filename, content_type)

    signatures = _SIGNATURES[media_type]
    if not any(content.startswith(sig) for sig in signatures):
        raise UnsupportedDocumentTypeError(filename, content_type)

    return media_type


def generate_storage_filename(media_type: MediaType) -> str:
    """A fresh, unpredictable filename — never derived from user input."""
    return f"{uuid4().hex}{_EXTENSION_BY_MEDIA_TYPE[media_type]}"


class DocumentStorage(ABC):
    """Persistence for raw document bytes, keyed by an opaque reference string."""

    @abstractmethod
    async def save(self, *, claim_id: str, filename: str, content: bytes) -> str:
        """Persist `content` and return a storage_reference to retrieve it later."""
        ...

    @abstractmethod
    async def read(self, storage_reference: str) -> bytes:
        """Retrieve previously saved content. Raises FileNotFoundError if missing."""
        ...


class LocalFileDocumentStorage(DocumentStorage):
    """
    Development storage: files under `<base_dir>/{claim_id}/{generated_name}`.

    `filename` is only ever used to derive the storage extension — never as
    a path component — so a malicious filename (e.g. "../../etc/passwd")
    can't escape `base_dir`. The claim_id is sanitised the same way for the
    same reason.
    """

    def __init__(self, base_dir: str = "data/uploads") -> None:
        self._base_dir = Path(base_dir)

    async def save(self, *, claim_id: str, filename: str, content: bytes) -> str:
        safe_claim_id = _sanitize_path_component(claim_id)
        claim_dir = self._base_dir / safe_claim_id
        claim_dir.mkdir(parents=True, exist_ok=True)

        extension = Path(filename).suffix.lower()
        if extension not in {".pdf", ".jpg", ".jpeg", ".png"}:
            extension = ""  # caller already validated content type; this is just a display hint
        generated_name = f"{uuid4().hex}{extension}"

        path = claim_dir / generated_name
        with open(path, "wb") as f:
            f.write(content)

        return f"{safe_claim_id}/{generated_name}"

    async def read(self, storage_reference: str) -> bytes:
        path = self._resolve(storage_reference)
        if not path.is_file():
            raise FileNotFoundError(f"No stored document at reference '{storage_reference}'.")
        with open(path, "rb") as f:
            return f.read()

    def _resolve(self, storage_reference: str) -> Path:
        # storage_reference is always something *we* generated (via save()),
        # never taken directly from a client — but resolve+contain defensively
        # in case a caller ever passes a malformed one.
        candidate = (self._base_dir / storage_reference).resolve()
        base = self._base_dir.resolve()
        if base not in candidate.parents and candidate != base:
            raise ValueError(f"Invalid storage reference: '{storage_reference}'.")
        return candidate


def _sanitize_path_component(value: str) -> str:
    """Keep only safe characters for a single path segment (no '..', '/', etc.)."""
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "_", value)
    return cleaned or "unknown"
