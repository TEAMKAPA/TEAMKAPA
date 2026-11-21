"""Punkt wejścia renderera: dispatch według zawartości, metadane dokumentu, błędy."""

import hashlib

import pytest

from backend.shared.schemas.quality import Severity
from backend.worker.vision.config import RenderConfig
from backend.worker.vision.errors import IssueCode, RenderError
from backend.worker.vision.models import DocumentKind, PageSource
from backend.worker.vision.render import render_document
from tests.vision.synth import (
    DOCM_TYPE,
    DOCX_TYPE,
    SynthPage,
    encode,
    make_odf,
    make_ole2,
    make_ooxml,
    make_pdf,
    stacked_pages,
    white,
)

CFG = RenderConfig(dpi=72)
PDF = make_pdf([SynthPage(), SynthPage(rotate=90)])


def test_pdf_document() -> None:
    doc = render_document(PDF, DocumentKind.PDF, CFG, isolated=False)
    assert doc.meta.sha256 == hashlib.sha256(PDF).hexdigest()
    assert (doc.meta.declared_kind, doc.meta.detected_kind) == (DocumentKind.PDF, DocumentKind.PDF)
    assert doc.meta.source_page_count == 2 and len(doc.pages) == 2
    assert doc.meta.issues == ()
    assert {"pypdfium2", "pdfium"} <= set(doc.meta.renderer_versions)
    assert {p.meta.source for p in doc.pages} == {PageSource.NATIVE_PDF}


def test_isolated_by_default() -> None:
    a = render_document(PDF, DocumentKind.PDF, CFG)
    b = render_document(PDF, DocumentKind.PDF, CFG, isolated=False)
    assert [p.meta for p in a.pages] == [p.meta for p in b.pages]


def test_declared_kind_mismatch_is_warning_and_content_wins() -> None:
    png = encode(white(100, 141))
    doc = render_document(png, DocumentKind.JPEG, CFG, isolated=False)
    assert doc.meta.detected_kind is DocumentKind.PNG
    [issue] = doc.meta.issues
    assert issue.code is IssueCode.DECLARED_KIND_MISMATCH and issue.severity is Severity.WARNING
    assert issue.detail == "declared=jpeg detected=png"


def test_stacked_image_document() -> None:
    image, _ = stacked_pages(100, 3)
    doc = render_document(encode(image), DocumentKind.PNG, CFG, isolated=False)
    assert doc.meta.source_page_count == 1 and len(doc.pages) == 3
    assert "pillow" in doc.meta.renderer_versions


OFFICE_SAMPLES = {
    DocumentKind.DOC: make_ole2(["WordDocument"]),
    DocumentKind.DOCX: make_ooxml(DOCX_TYPE),
    DocumentKind.ODS: make_odf("application/vnd.oasis.opendocument.spreadsheet"),
    DocumentKind.XLSX: make_ooxml(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"
    ),
}


@pytest.mark.parametrize("kind", list(OFFICE_SAMPLES))
def test_office_documents_are_not_rendered(kind: DocumentKind) -> None:
    """LibreOffice odrzucony: formaty biurowe nie przechodzą przez kanał wizualny."""
    with pytest.raises(RenderError) as info:
        render_document(OFFICE_SAMPLES[kind], kind, CFG, isolated=False)
    assert info.value.code is IssueCode.OFFICE_FORMAT_NOT_RENDERED
    assert info.value.detail == (
        f"kind={kind.value}: office formats are not rendered "
        f"declared={kind.value} detected={kind.value}"
    )
    assert info.value.issues == ()


def test_error_keeps_declared_kind_mismatch() -> None:
    """Obserwacja z A.8: ".DOC" z zawartością DOCX."""
    with pytest.raises(RenderError) as info:
        render_document(make_ooxml(DOCX_TYPE), DocumentKind.DOC, CFG, isolated=False)
    err = info.value
    assert err.code is IssueCode.OFFICE_FORMAT_NOT_RENDERED
    assert err.detail.endswith("declared=doc detected=docx")
    assert "kind=docx" in err.detail  # szczegóły pierwotnego błędu zostają
    [issue] = err.issues
    assert issue.code is IssueCode.DECLARED_KIND_MISMATCH
    assert issue.detail == "declared=doc detected=docx"
    assert isinstance(err.__cause__, RenderError)


@pytest.mark.parametrize("isolated", [False, True])
def test_render_failure_keeps_issues_and_kinds(isolated: bool) -> None:
    encrypted = make_pdf([SynthPage()], user_password="tajne")
    with pytest.raises(RenderError) as info:
        render_document(encrypted, DocumentKind.PNG, CFG, isolated=isolated)
    err = info.value
    assert err.code is IssueCode.PDF_ENCRYPTED
    assert err.detail.endswith("declared=png detected=pdf")
    assert [i.code for i in err.issues] == [IssueCode.DECLARED_KIND_MISMATCH]


@pytest.mark.parametrize(
    ("data", "detail"),
    [
        (b"plain text", "declared=none detected=unknown"),
        (make_ooxml(DOCM_TYPE), "declared=none detected=ooxml:docm"),
        (make_odf("application/vnd.oasis.opendocument.text"), "declared=none detected=odf:text"),
        (make_ole2(["Workbook"]), "declared=none detected=ole2:xls"),
    ],
)
def test_unsupported_formats(data: bytes, detail: str) -> None:
    with pytest.raises(RenderError) as info:
        render_document(data, None, CFG, isolated=False)
    assert (info.value.code, info.value.detail) == (IssueCode.UNSUPPORTED_FORMAT, detail)


def test_unsupported_format_reports_declared_kind() -> None:
    with pytest.raises(RenderError) as info:
        render_document(make_ooxml(DOCM_TYPE), DocumentKind.DOCX, CFG, isolated=False)
    assert info.value.detail == "declared=docx detected=ooxml:docm"
    assert info.value.issues == ()
