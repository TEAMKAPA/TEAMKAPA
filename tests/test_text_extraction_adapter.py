"""Adapter kroku 02 (#7) do kontraktu ``TextExtractor`` orkiestratora."""

from pathlib import Path
from typing import Any

import pytest

from backend.shared.schemas.pipeline import (
    AcroFormField,
    DocumentFormat,
    ExtractedPage,
    ExtractedTable,
    ExtractedTextBlock,
)
from backend.shared.schemas.pipeline import ExtractedDocument as Step02Document
from backend.shared.schemas.quality import BoundingBox, SourceChannel
from backend.worker.pipeline.errors import DocumentUnreadableError, PipelineStageError
from backend.worker.pipeline.extractors import (
    CorruptedFileError,
    EncryptedFileError,
    ExtractionError,
    FileTooLargeError,
    UnsupportedFileFormatError,
)
from backend.worker.pipeline.text_extraction import Step02TextExtractor, to_contract
from tests.pipeline_helpers import incoming, settings

pytestmark = pytest.mark.anyio

BOX = BoundingBox(x0=0.1, y0=0.1, x1=0.5, y1=0.2)


def _document(**overrides: Any) -> Step02Document:
    base: dict[str, Any] = {
        "document_sha256": "a" * 64,
        "filename": "document.pdf",
        "extension": "pdf",
        "mime_type": "application/pdf",
        "document_format": DocumentFormat.PDF,
        "page_count": 2,
    }
    return Step02Document(**(base | overrides))


def test_blocks_tables_and_acroform_become_elements() -> None:
    page1 = ExtractedPage(
        page_number=1,
        blocks=[
            ExtractedTextBlock(text="Wniosek o wydanie dowodu", page=1, bbox=BOX),
            ExtractedTextBlock(text="   ", page=1),
            ExtractedTextBlock(text="Numer PESEL", page=1, channel=SourceChannel.OCR,
                               confidence=0.83),
        ],
        tables=[ExtractedTable(page=1, rows=[["Imię", "", "Nazwisko"], ["", ""]], bbox=BOX)],
        acroform_fields=[
            AcroFormField(name="pesel", label="PESEL", field_type="text", page=1, bbox=BOX)
        ],
    )  # fmt: skip
    page2 = ExtractedPage(page_number=2, text="Adres zamieszkania\n\nKod pocztowy")
    contract = to_contract(_document(pages=[page1, page2], warnings=["strona 2 bez układu"]))

    assert [(e.element_id, e.page, e.text) for e in contract.elements] == [
        ("p1-b1", 1, "Wniosek o wydanie dowodu"),
        ("p1-b3", 1, "Numer PESEL"),
        ("p1-t1-r1", 1, "Imię | Nazwisko"),
        ("p1-f1", 1, "PESEL"),
        ("p2-l1", 2, "Adres zamieszkania"),
        ("p2-l3", 2, "Kod pocztowy"),
    ]
    by_id = {e.element_id: e for e in contract.elements}
    assert by_id["p1-b1"].bbox == BOX
    assert (by_id["p1-b3"].channel, by_id["p1-b3"].confidence) == (SourceChannel.OCR, 0.83)
    assert by_id["p1-f1"].channel is SourceChannel.ACROFORM
    assert by_id["p2-l1"].bbox is None
    assert contract.page_count == 2
    assert [i.code for i in contract.issues] == ["EXTRACTION_WARNING"]
    assert contract.as_step_payload()["text"].startswith("Wniosek o wydanie dowodu")


def test_full_text_fallback_without_pages() -> None:
    contract = to_contract(_document(page_count=0, full_text="Wniosek\nImię"))
    assert [e.text for e in contract.elements] == ["Wniosek", "Imię"]
    assert contract.page_count == 1


@pytest.mark.parametrize(
    "error",
    [
        EncryptedFileError("Plik chroniony hasłem"),
        CorruptedFileError("Plik uszkodzony"),
        UnsupportedFileFormatError("Nieobsługiwany format"),
        FileTooLargeError("Za duży"),
    ],
)
async def test_unreadable_files(tmp_path: Path, error: Exception) -> None:
    def failing(*_: object, **__: object) -> Step02Document:
        raise error

    with pytest.raises(DocumentUnreadableError, match=str(error)):
        await Step02TextExtractor(settings(), failing).extract(incoming(tmp_path))


async def test_other_extraction_errors_are_stage_errors(tmp_path: Path) -> None:
    def failing(*_: object, **__: object) -> Step02Document:
        raise ExtractionError("Docling: szczegóły", code="DOCLING_EXTRACTION_ERROR")

    with pytest.raises(PipelineStageError, match="DOCLING_EXTRACTION_ERROR") as exc:
        await Step02TextExtractor(settings(), failing).extract(incoming(tmp_path))
    assert "szczegóły" not in str(exc.value)


async def test_extractor_receives_content_and_neutral_name(tmp_path: Path) -> None:
    seen: dict[str, Any] = {}

    def fake(content: bytes, **kwargs: Any) -> Step02Document:
        seen.update(kwargs, content=content)
        return _document(full_text="Wniosek")

    document = incoming(tmp_path, b"%PDF-1.4 tresc")
    contract = await Step02TextExtractor(settings(), fake).extract(document)
    assert seen["content"] == b"%PDF-1.4 tresc"
    assert seen["filename"] == "document.pdf"  # nazwa użytkownika nie trafia do ekstraktora
    assert seen["mime_type"] == "application/pdf"
    assert contract.elements[0].text == "Wniosek"


@pytest.mark.parametrize(
    ("filename", "extension", "warned"),
    [("Wniosek.DOC", "docx", True), ("skan.JPE", "jpeg", False), ("skan.jpg", "jpg", False),
     ("wniosek.pdf", "pdf", False)],
)  # fmt: skip
async def test_content_based_type_is_reported(
    tmp_path: Path, filename: str, extension: str, warned: bool
) -> None:
    def fake(content: bytes, **kwargs: Any) -> Step02Document:
        return _document(full_text="Wniosek")

    document = incoming(tmp_path)
    ref = document.ref.model_copy(update={"filename": filename, "extension": extension})
    contract = await Step02TextExtractor(settings(), fake).extract(
        document.__class__(ref=ref, content=document.content, path=document.path)
    )
    codes = [i.code for i in contract.issues]
    assert ("FILE_TYPE_CORRECTED" in codes) is warned
