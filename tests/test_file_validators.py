"""Walidacja plików: reguły regulaminu + rozpoznawanie formatu z treści."""

import os
import sys

import pytest

from backend.api.file_validators import (
    CANONICAL_MIME,
    FileErrorCode,
    FileValidationError,
    libmagic_detector,
    mime_matches_extension,
    validate_filename,
    validate_upload,
)
from tests.file_samples import SAMPLES, docx, ods, pdf, png, sniff_mime, xlsx

MB = 1024 * 1024


def _error(callable_: object, *args: object, **kwargs: object) -> FileErrorCode:
    with pytest.raises(FileValidationError) as exc:
        callable_(*args, **kwargs)  # type: ignore[operator]
    return exc.value.code


# ------------------------------------------------------------------ nazwa pliku


@pytest.mark.parametrize(
    ("name", "extension"),
    [
        ("wniosek.pdf", "pdf"),
        ("Wniosek o nadpłatę (2024).PDF", "pdf"),
        ("skan.strona.1.jpeg", "jpeg"),
        ("zażółć gęślą jaźń.docx", "docx"),
        ("a" * 246 + ".pdf", "pdf"),  # dokładnie 250 znaków
    ],
)
def test_valid_filenames(name: str, extension: str) -> None:
    assert validate_filename(name) == (name, extension)


@pytest.mark.parametrize("char", list(";:><\\/|'*%$"))
def test_forbidden_characters(char: str) -> None:
    assert _error(validate_filename, f"wnio{char}sek.pdf") is FileErrorCode.FILENAME_FORBIDDEN_CHARS


@pytest.mark.parametrize("name", ["a\x00b.pdf", "a\nb.pdf", "a\x7fb.pdf"])
def test_control_characters_forbidden(name: str) -> None:
    assert _error(validate_filename, name) is FileErrorCode.FILENAME_FORBIDDEN_CHARS


def test_path_traversal_rejected() -> None:
    assert _error(validate_filename, "../etc/passwd.pdf") is FileErrorCode.FILENAME_FORBIDDEN_CHARS
    assert _error(validate_filename, "C:\\temp\\a.pdf") is FileErrorCode.FILENAME_FORBIDDEN_CHARS


def test_too_long() -> None:
    assert _error(validate_filename, "a" * 247 + ".pdf") is FileErrorCode.FILENAME_TOO_LONG


@pytest.mark.parametrize("name", [None, "", "   "])
def test_missing_name(name: str | None) -> None:
    assert _error(validate_filename, name) is FileErrorCode.FILENAME_MISSING


@pytest.mark.parametrize(
    "name", ["wniosek", "wniosek.exe", "wniosek.pdf.exe", ".pdf", " .pdf", "wniosek.", "a.txt"]
)
def test_extension_not_allowed(name: str) -> None:
    assert _error(validate_filename, name) is FileErrorCode.EXTENSION_NOT_ALLOWED


# ------------------------------------------------------------------ rozmiar i MIME


def test_empty_and_too_large() -> None:
    assert _error(validate_upload, "a.pdf", b"", max_bytes=MB, detect_mime=sniff_mime) is (
        FileErrorCode.FILE_EMPTY
    )
    big = pdf() + b"0" * MB
    assert _error(validate_upload, "a.pdf", big, max_bytes=MB, detect_mime=sniff_mime) is (
        FileErrorCode.FILE_TOO_LARGE
    )


@pytest.mark.parametrize("extension", sorted(SAMPLES))
def test_each_format_accepted(extension: str) -> None:
    content = SAMPLES[extension]()
    result = validate_upload(f"plik.{extension}", content, max_bytes=MB, detect_mime=sniff_mime)
    assert result.extension == extension
    assert result.mime_type == CANONICAL_MIME[extension]
    assert len(result.sha256) == 64
    assert result.size_bytes == len(content)


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("plik.pdf", png()),
        ("plik.png", pdf()),
        ("plik.docx", xlsx()),  # arkusz udający dokument
        ("plik.xlsx", docx()),
        ("plik.ods", docx()),
        ("plik.docx", ods()),
        ("plik.doc", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 1024),  # OLE bez Worda
        ("plik.docx", b"PK\x03\x04 to nie jest zip"),
    ],
)
def test_content_must_match_extension(name: str, content: bytes) -> None:
    assert _error(validate_upload, name, content, max_bytes=MB, detect_mime=sniff_mime) is (
        FileErrorCode.MIME_MISMATCH
    )


def test_exact_ooxml_mime_accepted_without_structure_check() -> None:
    assert mime_matches_extension("docx", CANONICAL_MIME["docx"], b"")


# ------------------------------------------------------------------ prawdziwa libmagic

_LIBMAGIC_REQUIRED = os.environ.get("REQUIRE_LIBMAGIC") == "1"
needs_libmagic = pytest.mark.skipif(
    sys.platform == "win32" and not _LIBMAGIC_REQUIRED,
    reason="libmagic na Windows (Git Bash) powoduje segfault — testy w CI/Dockerze",
)


@needs_libmagic
@pytest.mark.parametrize("extension", sorted(SAMPLES))
def test_libmagic_accepts_real_samples(extension: str) -> None:
    content = SAMPLES[extension]()
    result = validate_upload(
        f"plik.{extension}", content, max_bytes=MB, detect_mime=libmagic_detector
    )
    assert result.mime_type == CANONICAL_MIME[extension]


@needs_libmagic
def test_libmagic_rejects_renamed_file() -> None:
    assert (
        _error(validate_upload, "plik.pdf", png(), max_bytes=MB, detect_mime=libmagic_detector)
        is FileErrorCode.MIME_MISMATCH
    )
