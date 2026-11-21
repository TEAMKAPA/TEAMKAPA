"""Rozpoznanie rodzaju dokumentu z zawartości (dane syntetyczne)."""

import pytest

from backend.shared.schemas.quality import Severity
from backend.worker.vision.errors import IssueCode
from backend.worker.vision.models import DocumentKind
from backend.worker.vision.render.detect import (
    declared_kind_issues,
    detect_kind,
    kind_from_extension,
)
from tests.vision.synth import (
    DOCM_TYPE,
    DOCX_TYPE,
    XLSM_TYPE,
    XLSX_TYPE,
    make_odf,
    make_ole2,
    make_ooxml,
    make_zip,
)

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32


@pytest.mark.parametrize(
    ("data", "kind", "detail"),
    [
        (b"%PDF-1.7\n%...", DocumentKind.PDF, "pdf"),
        (b"\x00" * 500 + b"%PDF-1.4\n", DocumentKind.PDF, "pdf"),  # nagłówek w oknie 1024 B
        (PNG, DocumentKind.PNG, "png"),
        (PNG + b"%PDF-1.4", DocumentKind.PNG, "png"),  # sygnatura od bajtu 0 ma pierwszeństwo
        (JPEG, DocumentKind.JPEG, "jpeg"),
        (b"\x00" * 1100 + b"%PDF-1.4\n", None, "unknown"),  # poza oknem nagłówka
        (b"", None, "unknown"),
        (b"plain text", None, "unknown"),
    ],
)
def test_signatures(data: bytes, kind: DocumentKind | None, detail: str) -> None:
    d = detect_kind(data)
    assert (d.kind, d.detail) == (kind, detail)


@pytest.mark.parametrize(
    ("content_type", "kind", "detail"),
    [
        (DOCX_TYPE, DocumentKind.DOCX, "ooxml:docx"),
        (XLSX_TYPE, DocumentKind.XLSX, "ooxml:xlsx"),
        (DOCM_TYPE, None, "ooxml:docm"),  # z makrami: odrzucony jawnie
        (XLSM_TYPE, None, "ooxml:xlsm"),
        ("application/xml", None, "ooxml:unknown"),
    ],
)
def test_ooxml_by_content_types(content_type: str, kind: DocumentKind | None, detail: str) -> None:
    d = detect_kind(make_ooxml(content_type))
    assert (d.kind, d.container, d.detail) == (kind, "zip", detail)


def test_odf_spreadsheet_and_text() -> None:
    ods = detect_kind(make_odf("application/vnd.oasis.opendocument.spreadsheet"))
    assert (ods.kind, ods.detail) == (DocumentKind.ODS, "odf:spreadsheet")
    odt = detect_kind(make_odf("application/vnd.oasis.opendocument.text"))
    assert (odt.kind, odt.detail) == (None, "odf:text")


def test_other_and_broken_zip() -> None:
    assert detect_kind(make_zip({"a.txt": b"x"})).detail == "zip:other"
    assert detect_kind(b"PK\x03\x04" + b"\x00" * 64).detail == "zip:unreadable"


@pytest.mark.parametrize(
    ("streams", "kind", "detail"),
    [
        (["WordDocument", "1Table"], DocumentKind.DOC, "ole2:doc"),
        (["Workbook"], None, "ole2:xls"),
        (["EncryptedPackage", "EncryptionInfo"], None, "ole2:encrypted_ooxml"),
        (["Something"], None, "ole2:other"),
        # więcej wpisów niż mieści jeden sektor katalogu (łańcuch przez FAT)
        ([f"s{i}" for i in range(9)] + ["WordDocument"], DocumentKind.DOC, "ole2:doc"),
    ],
)
def test_ole2_by_stream_names(streams: list[str], kind: DocumentKind | None, detail: str) -> None:
    d = detect_kind(make_ole2(streams))
    assert (d.kind, d.container, d.detail) == (kind, "ole2", detail)


def test_ole2_stream_name_in_content_is_not_enough() -> None:
    # nazwa "WordDocument" w treści (poza katalogiem) nie może dać rozpoznania DOC
    data = make_ole2(["Workbook"]) + "WordDocument".encode("utf-16-le")
    assert detect_kind(data).detail == "ole2:xls"


@pytest.mark.parametrize(
    "data",
    [
        make_ole2(["WordDocument"])[:600],  # obcięty plik
        make_ole2(["a", "b", "c", "d", "WordDocument"], directory_loop=True),  # pętla katalogu
        b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\xff" * 600,  # śmieci w nagłówku
    ],
)
def test_ole2_malformed_is_unreadable(data: bytes) -> None:
    d = detect_kind(data)
    assert (d.kind, d.detail) == (None, "ole2:unreadable")


@pytest.mark.parametrize(
    ("name", "kind"),
    [
        ("formularz.pdf", DocumentKind.PDF),
        ("FORMULARZ.PDF", DocumentKind.PDF),
        ("a.DOC", DocumentKind.DOC),
        ("a.docx", DocumentKind.DOCX),
        ("a.ODS", DocumentKind.ODS),
        ("a.xlsx", DocumentKind.XLSX),
        ("a.JPG", DocumentKind.JPEG),
        ("a.jpeg", DocumentKind.JPEG),
        ("a.png", DocumentKind.PNG),
        ("a.JPE", None),  # spoza listy regulaminu: decyzja należy do API
        ("a.odt", None),
        ("bez_rozszerzenia", None),
        ("nazwa ze spacją .docx", DocumentKind.DOCX),
    ],
)
def test_kind_from_extension(name: str, kind: DocumentKind | None) -> None:
    assert kind_from_extension(name) == kind


def test_declared_kind_mismatch_is_warning() -> None:
    detected = detect_kind(make_ooxml(DOCX_TYPE))
    [issue] = declared_kind_issues(DocumentKind.DOC, detected)
    assert issue.code is IssueCode.DECLARED_KIND_MISMATCH
    assert issue.severity is Severity.WARNING
    assert issue.detail == "declared=doc detected=docx"


def test_declared_kind_match_or_unknown_gives_no_issue() -> None:
    detected = detect_kind(make_ooxml(DOCX_TYPE))
    assert declared_kind_issues(DocumentKind.DOCX, detected) == []
    assert declared_kind_issues(None, detected) == []
    assert declared_kind_issues(DocumentKind.PDF, detect_kind(b"???")) == []
