"""Adapter ekstrakcji tekstu z #7 (``step02_text_extract``) do kontraktu ``TextExtractor``.

Krok 02 zwraca bogaty model (``backend.shared.schemas.pipeline.ExtractedDocument``: strony,
bloki z bbox, tabele, pola AcroForm, słowa OCR). Orkiestrator i etapy 05–09 potrzebują
płaskiej listy elementów z pozycją — adapter ją buduje, nie gubiąc źródeł:

- blok tekstu → element ``p{strona}-b{n}`` (bbox, kanał TEXT/OCR, pewność OCR),
- wiersz tabeli → element ``p{strona}-t{n}-r{m}`` (bbox tabeli),
- pole AcroForm → element ``p{strona}-f{n}`` (kanał ACROFORM),
- strona bez bloków (np. DOCX bez układu) → linie tekstu strony bez bbox.

Błędy: plik zaszyfrowany / uszkodzony / nieobsługiwany / za duży → ``DocumentUnreadableError``
(błąd ostateczny), pozostałe błędy ekstrakcji → ``PipelineStageError``.
"""

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from backend.shared.config import Settings
from backend.shared.schemas.pipeline import ExtractedDocument as Step02Document
from backend.shared.schemas.pipeline import ExtractedPage
from backend.shared.schemas.quality import Issue, Severity, SourceChannel
from backend.worker.document import IncomingDocument
from backend.worker.pipeline.contracts import ExtractedDocument, TextElement
from backend.worker.pipeline.errors import DocumentUnreadableError, PipelineStageError
from backend.worker.pipeline.extractors import (
    CorruptedFileError,
    EncryptedFileError,
    ExtractionError,
    FileTooLargeError,
    UnsupportedFileFormatError,
)

logger = logging.getLogger(__name__)

STAGE = "odczyt treści dokumentu"
#: Rozszerzenia JPEG są równoważne — zmiana nazwy nie jest korektą rodzaju pliku.
_JPEG_NAMES = {"jpe": "jpeg", "jpg": "jpeg"}
_UNREADABLE = (
    CorruptedFileError,
    EncryptedFileError,
    UnsupportedFileFormatError,
    FileTooLargeError,
)

ExtractFunction = Callable[..., Step02Document]


def _page_elements(page: ExtractedPage) -> list[TextElement]:
    number = page.page_number
    elements: list[TextElement] = []
    for index, block in enumerate(page.blocks, start=1):
        if block.text.strip():
            elements.append(
                TextElement(
                    element_id=f"p{number}-b{index}",
                    page=number,
                    text=block.text.strip(),
                    bbox=block.bbox,
                    channel=block.channel,
                    confidence=block.confidence,
                )
            )
    for t, table in enumerate(page.tables, start=1):
        for r, row in enumerate(table.rows, start=1):
            text = " | ".join(cell.strip() for cell in row if cell and cell.strip())
            if text:
                elements.append(
                    TextElement(element_id=f"p{number}-t{t}-r{r}", page=number, text=text,
                                bbox=table.bbox)
                )  # fmt: skip
    for f, field in enumerate(page.acroform_fields, start=1):
        label = (field.label or field.name or "").strip()
        if label:
            elements.append(
                TextElement(element_id=f"p{number}-f{f}", page=number, text=label,
                            bbox=field.bbox, channel=SourceChannel.ACROFORM)
            )  # fmt: skip
    if not elements and page.text.strip():
        elements = [
            TextElement(element_id=f"p{number}-l{i}", page=number, text=line.strip())
            for i, line in enumerate(page.text.splitlines(), start=1)
            if line.strip()
        ]
    return elements


def to_contract(result: Step02Document) -> ExtractedDocument:
    """Model kroku 02 → kontrakt ``ExtractedDocument`` orkiestratora."""
    elements = [element for page in result.pages for element in _page_elements(page)]
    if not elements and result.full_text.strip():
        elements = [
            TextElement(element_id=f"p1-l{i}", page=1, text=line.strip())
            for i, line in enumerate(result.full_text.splitlines(), start=1)
            if line.strip()
        ]
    return ExtractedDocument(
        page_count=max(result.page_count, 1),
        elements=elements,
        issues=[
            Issue(code="EXTRACTION_WARNING", severity=Severity.INFO, message=warning)
            for warning in result.warnings
        ],
    )


class Step02TextExtractor:
    """``TextExtractor`` oparty na kroku 02 (#7): PDF, DOC/DOCX, ODS, XLSX, JPG/PNG."""

    name = "krok 02 (#7): Docling, pypdfium2, Tesseract"

    def __init__(self, settings: Settings, extract: ExtractFunction | None = None) -> None:
        self._settings = settings
        self._extract = extract

    def _function(self) -> ExtractFunction:
        if self._extract is not None:
            return self._extract
        from backend.worker.pipeline.step02_text_extract import extract_document

        return extract_document

    async def extract(self, document: IncomingDocument) -> ExtractedDocument:
        function = self._function()
        kwargs: dict[str, Any] = {
            # Neutralna nazwa: format wynika z rozszerzenia; nazwa użytkownika nie trafia do logów.
            "filename": f"document.{document.ref.extension}",
            "mime_type": document.ref.mime_type,
            "settings": self._settings,
        }
        try:
            result = await asyncio.to_thread(function, document.content, **kwargs)
        except _UNREADABLE as exc:
            raise DocumentUnreadableError(exc.message) from exc
        except ExtractionError as exc:
            logger.warning("Ekstrakcja %s: %s (%s)", document.ref.job_id, exc.code, exc.message)
            raise PipelineStageError(STAGE, f"błąd ekstrakcji treści ({exc.code})") from exc
        contract = to_contract(result)
        declared = document.ref.filename.rpartition(".")[2].lower()
        effective = document.ref.extension
        if _JPEG_NAMES.get(declared, declared) == _JPEG_NAMES.get(effective, effective):
            return contract
        # API przyjęło plik pokrewnego rodzaju (np. .doc z treścią DOCX) — urzędnik ma to wiedzieć.
        corrected = Issue(
            code="FILE_TYPE_CORRECTED",
            severity=Severity.WARNING,
            message=f"Plik z rozszerzeniem .{declared} ma zawartość {effective.upper()} "
            "— przetworzono według zawartości",
        )
        return contract.model_copy(update={"issues": [*contract.issues, corrected]})
