"""Modele pośrednie pipeline'u przetwarzania dokumentu (etapy 1–10).

W szczególności ``ExtractedDocument`` stanowi kontrakt wyjścia etapu 02
(ekstrakcja tekstu, tabel, metadanych i pól AcroForm) i wejścia kolejnych etapów
analizy wizualnej, fuzji oraz klasyfikacji dokumentu.
"""

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from backend.shared.schemas.quality import BoundingBox, Fraction, SourceChannel


class PipelineModel(BaseModel):
    """Bazowy model struktur pośrednich pipeline'u (niemutowalny, bez nieznanych kluczy)."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class DocumentFormat(StrEnum):
    """Kategoria formatu dokumentu wejściowego rozpoznana w etapie ekstrakcji."""

    PDF = "PDF"
    IMAGE = "IMAGE"
    DOCX = "DOCX"
    XLSX = "XLSX"
    ODS = "ODS"
    DOC = "DOC"


class ExtractedTextBlock(PipelineModel):
    """Pojedynczy blok tekstowy wyekstrahowany z dokumentu."""

    text: str
    page: Annotated[int, Field(ge=1)]
    bbox: BoundingBox | None = None
    channel: SourceChannel = SourceChannel.TEXT
    confidence: Fraction | None = None
    heading_level: Annotated[int, Field(ge=1, le=6)] | None = None


class ExtractedTableCell(PipelineModel):
    """Komórka tabeli z opcjonalnym scaleniem wierszy/kolumn."""

    row: Annotated[int, Field(ge=0)]
    col: Annotated[int, Field(ge=0)]
    row_span: Annotated[int, Field(ge=1)] = 1
    col_span: Annotated[int, Field(ge=1)] = 1
    text: str = ""


class ExtractedTable(PipelineModel):
    """Tabela lub arkusz wyekstrahowany z dokumentu."""

    page: Annotated[int, Field(ge=1)]
    rows: list[list[str]] = Field(default_factory=list)
    cells: list[ExtractedTableCell] = Field(default_factory=list)
    bbox: BoundingBox | None = None
    sheet_name: str | None = None


class AcroFormField(PipelineModel):
    """Interaktywne pole formularza PDF (AcroForm)."""

    name: str
    field_type: str
    page: Annotated[int, Field(ge=1)]
    label: str | None = None
    value: str | None = None
    options: list[str] = Field(default_factory=list)
    required: bool = False
    read_only: bool = False
    bbox: BoundingBox | None = None


class ExtractedPage(PipelineModel):
    """Wyekstrahowana treść pojedynczej strony (lub arkusza) dokumentu."""

    page_number: Annotated[int, Field(ge=1)]
    width: float | None = Field(default=None, gt=0.0)
    height: float | None = Field(default=None, gt=0.0)
    text: str = ""
    blocks: list[ExtractedTextBlock] = Field(default_factory=list)
    tables: list[ExtractedTable] = Field(default_factory=list)
    acroform_fields: list[AcroFormField] = Field(default_factory=list)
    needs_ocr: bool = False
    ocr_applied: bool = False
    ocr_confidence: Fraction | None = None
    source_channels: list[SourceChannel] = Field(default_factory=list)


class ExtractedDocument(PipelineModel):
    """Ujednolicony wynik ekstrakcji dokumentu wejściowego (wyjście kroku 02)."""

    document_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    filename: str
    extension: str
    mime_type: str
    document_format: DocumentFormat
    page_count: Annotated[int, Field(ge=0)]
    full_text: str = ""
    markdown: str = ""
    pages: list[ExtractedPage] = Field(default_factory=list)
    tables: list[ExtractedTable] = Field(default_factory=list)
    acroform_fields: list[AcroFormField] = Field(default_factory=list)
    source_channels: list[SourceChannel] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, str] = Field(default_factory=dict)
