"""
Unit tests for app/storage/document_storage.py — upload validation and
LocalFileDocumentStorage. Exercises real filesystem I/O against a
temporary directory, not mocked.
"""

from __future__ import annotations

import shutil

import pytest

from app.ai.schemas.ai_schemas import MediaType
from app.domain.errors import DocumentTooLargeError, EmptyDocumentError, UnsupportedDocumentTypeError
from app.storage.document_storage import (
    LocalFileDocumentStorage,
    generate_storage_filename,
    validate_upload,
)

TEST_DIR = "data/test_document_storage"

JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 100
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
PDF_BYTES = b"%PDF-1.4\n" + b"x" * 100


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def storage():
    s = LocalFileDocumentStorage(base_dir=TEST_DIR)
    yield s
    shutil.rmtree(TEST_DIR, ignore_errors=True)


class TestValidateUpload:
    def test_accepts_valid_jpeg(self):
        assert validate_upload(filename="rx.jpg", content_type="image/jpeg", content=JPEG_BYTES) == MediaType.JPEG

    def test_accepts_jpg_content_type_alias(self):
        assert validate_upload(filename="rx.jpg", content_type="image/jpg", content=JPEG_BYTES) == MediaType.JPEG

    def test_accepts_valid_png(self):
        assert validate_upload(filename="rx.png", content_type="image/png", content=PNG_BYTES) == MediaType.PNG

    def test_accepts_valid_pdf(self):
        assert validate_upload(filename="bill.pdf", content_type="application/pdf", content=PDF_BYTES) == MediaType.PDF

    def test_rejects_unsupported_content_type(self):
        with pytest.raises(UnsupportedDocumentTypeError):
            validate_upload(filename="x.txt", content_type="text/plain", content=b"hello world")

    def test_rejects_missing_content_type(self):
        with pytest.raises(UnsupportedDocumentTypeError):
            validate_upload(filename="x.jpg", content_type=None, content=JPEG_BYTES)

    def test_rejects_empty_content(self):
        with pytest.raises(EmptyDocumentError):
            validate_upload(filename="x.jpg", content_type="image/jpeg", content=b"")

    def test_rejects_oversized_content(self):
        with pytest.raises(DocumentTooLargeError):
            validate_upload(
                filename="x.jpg", content_type="image/jpeg", content=JPEG_BYTES, max_bytes=10
            )

    def test_rejects_content_that_does_not_match_declared_type(self):
        """A .jpg extension/content-type claim with non-JPEG bytes — the
        magic-byte check catches a mislabeled or spoofed upload."""
        with pytest.raises(UnsupportedDocumentTypeError):
            validate_upload(filename="fake.jpg", content_type="image/jpeg", content=b"not a real jpeg at all")

    def test_error_is_recoverable(self):
        try:
            validate_upload(filename="x.txt", content_type="text/plain", content=b"x")
        except UnsupportedDocumentTypeError as exc:
            assert exc.recoverable is True


class TestGenerateStorageFilename:
    def test_filenames_are_unique(self):
        names = {generate_storage_filename(MediaType.JPEG) for _ in range(20)}
        assert len(names) == 20

    def test_extension_matches_media_type(self):
        assert generate_storage_filename(MediaType.PDF).endswith(".pdf")
        assert generate_storage_filename(MediaType.JPEG).endswith(".jpg")
        assert generate_storage_filename(MediaType.PNG).endswith(".png")


class TestLocalFileDocumentStorage:
    @pytest.mark.anyio
    async def test_save_and_read_round_trip(self, storage):
        ref = await storage.save(claim_id="CLM-TEST", filename="rx.jpg", content=JPEG_BYTES)
        data = await storage.read(ref)
        assert data == JPEG_BYTES

    @pytest.mark.anyio
    async def test_storage_reference_is_not_the_original_filename(self, storage):
        """Never trust/reuse the client-supplied filename as a storage path."""
        ref = await storage.save(claim_id="CLM-TEST", filename="../../etc/passwd.jpg", content=JPEG_BYTES)
        assert "passwd" not in ref
        assert ".." not in ref

    @pytest.mark.anyio
    async def test_different_claims_get_separate_directories(self, storage):
        ref_a = await storage.save(claim_id="CLM-A", filename="rx.jpg", content=JPEG_BYTES)
        ref_b = await storage.save(claim_id="CLM-B", filename="rx.jpg", content=PNG_BYTES)
        assert ref_a.startswith("CLM-A/")
        assert ref_b.startswith("CLM-B/")
        assert await storage.read(ref_a) == JPEG_BYTES
        assert await storage.read(ref_b) == PNG_BYTES

    @pytest.mark.anyio
    async def test_reading_unknown_reference_raises(self, storage):
        with pytest.raises(FileNotFoundError):
            await storage.read("CLM-TEST/does-not-exist.jpg")

    @pytest.mark.anyio
    async def test_path_traversal_reference_is_rejected(self, storage):
        with pytest.raises((ValueError, FileNotFoundError)):
            await storage.read("../../../../etc/passwd")

    @pytest.mark.anyio
    async def test_claim_id_is_sanitized(self, storage):
        ref = await storage.save(claim_id="../../evil", filename="rx.jpg", content=JPEG_BYTES)
        assert ".." not in ref
        # still readable via the same (sanitized) reference
        assert await storage.read(ref) == JPEG_BYTES
