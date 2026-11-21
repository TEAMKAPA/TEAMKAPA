"""Testy jednostkowe szkieletu etapu 02 (routing formatów, wyjątki, czyszczenie plików)."""

import io
import zipfile
from pathlib import Path

import pytest

from backend.shared.config import Settings
from backend.shared.schemas.pipeline import DocumentFormat, ExtractedDocument
from backend.worker.pipeline.extractors import ExtractionContext, temporary_document_file
from backend.worker.pipeline.step02_text_extract import (
    CorruptedFileError,
    EncryptedFileError,
    FileTooLargeError,
    UnsupportedFileFormatError,
    extract_document,
)

_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def _make_zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _minimal_pdf(*, encrypted: bool = False) -> bytes:
    body = b"%PDF-1.7\n1 0 obj << /Type /Catalog >> endobj\n"
    if encrypted:
        body += b"2 0 obj << /Encrypt 3 0 R >> endobj\n"
    return body + b"%%EOF\n"


def _minimal_docx() -> bytes:
    return _make_zip(
        {
            "[Content_Types].xml": b"<Types/>",
            "word/document.xml": b"<w:document/>",
        }
    )


def _minimal_xlsx() -> bytes:
    return _make_zip(
        {
            "[Content_Types].xml": b"<Types/>",
            "xl/workbook.xml": b"<workbook/>",
        }
    )


def _minimal_ods(*, encrypted: bool = False) -> bytes:
    entries: dict[str, bytes] = {
        "mimetype": b"application/vnd.oasis.opendocument.spreadsheet",
        "content.xml": b"<office:document-content/>",
    }
    if encrypted:
        entries["META-INF/manifest.xml"] = b"<manifest><encryption-data/></manifest>"
    return _make_zip(entries)


def _minimal_doc(*, encrypted: bool = False) -> bytes:
    fib = b"\xec\xa5" + b"\x00" * 8 + ((0x0100 if encrypted else 0x0000).to_bytes(2, "little"))
    return _OLE_MAGIC + b"\x00" * 16 + "WordDocument".encode("utf-16-le") + fib


@pytest.mark.parametrize(
    ("filename", "mime_type", "payload", "expected_format"),
    [
        ("wniosek.pdf", "application/pdf", _minimal_pdf(), DocumentFormat.PDF),
        ("skan.jpg", "image/jpeg", b"\xff\xd8\xff\xe0\x00\x10JFIF", DocumentFormat.IMAGE),
        ("skan.jpeg", "image/jpeg", b"\xff\xd8\xff\xe0\x00\x10JFIF", DocumentFormat.IMAGE),
        ("skan.png", "image/png", b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR", DocumentFormat.IMAGE),
        (
            "form.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            _minimal_docx(),
            DocumentFormat.DOCX,
        ),
        ("form.docx", "application/zip", _minimal_docx(), DocumentFormat.DOCX),
        (
            "tabela.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            _minimal_xlsx(),
            DocumentFormat.XLSX,
        ),
        (
            "arkusz.ods",
            "application/vnd.oasis.opendocument.spreadsheet",
            _minimal_ods(),
            DocumentFormat.ODS,
        ),
        ("stary.doc", "application/msword", _minimal_doc(), DocumentFormat.DOC),
    ],
)
def test_extract_document_routes_supported_formats(
    tmp_path: Path,
    filename: str,
    mime_type: str,
    payload: bytes,
    expected_format: DocumentFormat,
) -> None:
    settings = Settings(temp_dir=tmp_path)

    result_bytes = extract_document(
        payload, filename=filename, mime_type=mime_type, settings=settings
    )
    result_stream = extract_document(
        io.BytesIO(payload), filename=filename, mime_type=mime_type, settings=settings
    )

    assert isinstance(result_bytes, ExtractedDocument)
    assert result_bytes.document_format is expected_format
    assert result_bytes.document_sha256 == result_stream.document_sha256
    assert list(tmp_path.iterdir()) == []


def test_extract_document_resolves_from_mime_only(tmp_path: Path) -> None:
    settings = Settings(temp_dir=tmp_path)
    result = extract_document(_minimal_pdf(), mime_type="application/pdf", settings=settings)
    assert result.document_format is DocumentFormat.PDF
    assert result.extension == "pdf"


def test_extract_document_rejects_unsupported_and_mismatched_formats() -> None:
    with pytest.raises(UnsupportedFileFormatError):
        extract_document(b"hello", filename="notes.txt")

    with pytest.raises(UnsupportedFileFormatError):
        extract_document(b"hello", mime_type="text/plain")

    with pytest.raises(UnsupportedFileFormatError):
        extract_document(b"hello")

    with pytest.raises(UnsupportedFileFormatError):
        extract_document(_minimal_pdf(), filename="form.pdf", mime_type="image/png")


def test_extract_document_detects_corrupted_files() -> None:
    with pytest.raises(CorruptedFileError):
        extract_document(b"", filename="empty.pdf")

    with pytest.raises(CorruptedFileError):
        extract_document(b"not a pdf", filename="broken.pdf")

    with pytest.raises(CorruptedFileError):
        extract_document(b"not a png", filename="broken.png")

    with pytest.raises(CorruptedFileError):
        extract_document(b"not a zip", filename="broken.docx")

    with pytest.raises(CorruptedFileError):
        extract_document(_make_zip({"other.txt": b"x"}), filename="broken.xlsx")

    with pytest.raises(CorruptedFileError):
        extract_document(b"not an ods", filename="broken.ods")

    with pytest.raises(CorruptedFileError):
        extract_document(b"not a doc", filename="broken.doc")


def test_extract_document_detects_encrypted_files() -> None:
    with pytest.raises(EncryptedFileError):
        extract_document(_minimal_pdf(encrypted=True), filename="locked.pdf")

    encrypted_ooxml = _OLE_MAGIC + "EncryptedPackage".encode("utf-16-le")
    with pytest.raises(EncryptedFileError):
        extract_document(encrypted_ooxml, filename="locked.docx")

    with pytest.raises(EncryptedFileError):
        extract_document(_minimal_ods(encrypted=True), filename="locked.ods")

    with pytest.raises(EncryptedFileError):
        extract_document(_minimal_doc(encrypted=True), filename="locked.doc")


def test_extract_document_enforces_size_limit() -> None:
    settings = Settings(max_file_size_mb=1)
    oversized = _minimal_pdf() + (b"A" * (1024 * 1024 + 1))
    with pytest.raises(FileTooLargeError):
        extract_document(oversized, filename="huge.pdf", settings=settings)


def test_temporary_document_file_cleans_up_on_success_and_error(tmp_path: Path) -> None:
    created_path: Path | None = None
    with temporary_document_file(b"abc", extension="pdf", temp_dir=tmp_path) as path:
        created_path = path
        assert path.exists()
        assert path.read_bytes() == b"abc"
        assert path.parent.parent == tmp_path
    assert created_path is not None
    assert not created_path.exists()
    assert list(tmp_path.iterdir()) == []

    failed_path: Path | None = None
    with (
        pytest.raises(RuntimeError, match="boom"),
        temporary_document_file(b"xyz", extension="docx", temp_dir=tmp_path) as path,
    ):
        failed_path = path
        assert path.exists()
        raise RuntimeError("boom")
    assert failed_path is not None
    assert not failed_path.exists()
    assert list(tmp_path.iterdir()) == []


def test_custom_handler_error_leaves_no_temp_files(tmp_path: Path) -> None:
    settings = Settings(temp_dir=tmp_path)

    def failing_handler(ctx: ExtractionContext) -> ExtractedDocument:
        with temporary_document_file(
            ctx.content, extension=ctx.extension, temp_dir=ctx.settings.temp_dir
        ) as doc_path:
            assert doc_path.exists()
            raise CorruptedFileError("Uszkodzony plik podczas parsowania.")

    with pytest.raises(CorruptedFileError):
        extract_document(
            _minimal_pdf(),
            filename="fail.pdf",
            settings=settings,
            handlers={DocumentFormat.PDF: failing_handler},
        )
    assert list(tmp_path.iterdir()) == []
