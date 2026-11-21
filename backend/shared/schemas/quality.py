"""Raport jakości i wynik analizy — nasze rozszerzenie, poza plikiem importu COI.

Schemat COI nie ma miejsca na źródła i pewność pól, dlatego API zwraca kopertę
``AnalysisResult``: ``form`` (czysty plik importu COI) + ``report`` (pewność, źródła, problemy).
Pola raportu wiążą się z polami formularza przez ``field_id`` (UUID pola w ``form``).
"""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.shared.schemas.coi_form import FormDefinition

Fraction = Annotated[float, Field(ge=0.0, le=1.0)]


class ReportModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Severity(StrEnum):
    ERROR = "ERROR"  # formularz nie spełnia reguły COI — nie nadaje się do importu
    WARNING = "WARNING"  # prawdopodobny problem — urzędnik powinien sprawdzić
    INFO = "INFO"


class Issue(ReportModel):
    """Pojedynczy problem wykryty przez walidację lub kontrolę jakości."""

    code: str = Field(description="Stabilny kod, np. FINISH_TAB_LAST, STATE_UNKNOWN_FIELD")
    severity: Severity
    message: str
    path: str = Field(default="", description="Ścieżka JSON Pointer w pliku COI, np. /tabs/0")
    field_id: UUID | None = None


class ConfidenceLevel(StrEnum):
    """Kolor w interfejsie urzędnika."""

    HIGH = "HIGH"  # zielony — nie wymaga sprawdzenia
    MEDIUM = "MEDIUM"  # żółty — do przejrzenia
    LOW = "LOW"  # czerwony — wymaga decyzji urzędnika


def confidence_level(score: float, *, high: float, low: float) -> ConfidenceLevel:
    """Progi pochodzą z konfiguracji (CONFIDENCE_HIGH / CONFIDENCE_LOW) i są kalibrowane."""
    if not 0.0 <= low <= high <= 1.0:
        raise ValueError("wymagane 0 <= low <= high <= 1")
    if score >= high:
        return ConfidenceLevel.HIGH
    if score >= low:
        return ConfidenceLevel.MEDIUM
    return ConfidenceLevel.LOW


class SourceChannel(StrEnum):
    TEXT = "TEXT"  # warstwa tekstowa (Docling)
    ACROFORM = "ACROFORM"  # pole formularza PDF
    OCR = "OCR"  # Tesseract
    VISUAL = "VISUAL"  # OpenCV / VLM
    RULE = "RULE"  # reguła deterministyczna lub standardowa sekcja COI


class BoundingBox(ReportModel):
    """Prostokąt we współrzędnych ułamkowych strony (0..1), początek w lewym górnym rogu."""

    x0: Fraction
    y0: Fraction
    x1: Fraction
    y1: Fraction

    @model_validator(mode="after")
    def _ordered(self) -> BoundingBox:
        if self.x1 < self.x0 or self.y1 < self.y0:
            raise ValueError("bbox: wymagane x0 <= x1 i y0 <= y1")
        return self


class FieldSource(ReportModel):
    """Miejsce w dokumencie wejściowym, z którego pochodzi pole."""

    channel: SourceChannel
    page: Annotated[int, Field(ge=1)] | None = Field(
        default=None, description="Numer strony dokumentu, od 1"
    )
    bbox: BoundingBox | None = None
    text: str | None = Field(default=None, description="Etykieta/tekst w dokumencie")
    element_id: str | None = Field(default=None, description="ID elementu wizualnego (cv-*/vlm-*)")


class FieldAssessment(ReportModel):
    """Ocena jednego pola (lub sekcji) wygenerowanego formularza."""

    field_id: UUID
    tech_name: str
    confidence: Fraction
    level: ConfidenceLevel
    sources: list[FieldSource] = Field(default_factory=list)
    signals: dict[str, Fraction] = Field(
        default_factory=dict,
        description="Składowe pewności, np. source_found, visual_match, dual_read, ocr",
    )
    issues: list[Issue] = Field(default_factory=list)


class SkippedElement(ReportModel):
    """Element dokumentu celowo pominięty w formularzu (np. „wypełnia urząd”, podpis)."""

    reason: str
    source: FieldSource


class QualityReport(ReportModel):
    document_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    document_type: str | None = None
    accuracy_mode: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    fields: list[FieldAssessment] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list, description="Problemy poziomu formularza")
    skipped_elements: list[SkippedElement] = Field(default_factory=list)
    repair_iterations: Annotated[int, Field(ge=0)] = 0
    models: dict[str, str] = Field(default_factory=dict, description="rola -> nazwa modelu")

    @property
    def needs_review(self) -> int:
        return sum(1 for f in self.fields if f.level is not ConfidenceLevel.HIGH)

    @property
    def has_errors(self) -> bool:
        return any(i.severity is Severity.ERROR for i in self.issues) or any(
            i.severity is Severity.ERROR for f in self.fields for i in f.issues
        )


class AnalysisResult(ReportModel):
    """Odpowiedź API: plik importu COI + raport jakości."""

    form: FormDefinition
    report: QualityReport
