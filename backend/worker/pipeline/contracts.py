"""Kontrakty etapów pipeline'u — wejście i wyjście każdego etapu jako modele Pydantic.

Kanały odczytu dokumentu (uruchamiane równolegle):
- ``TextExtractor`` (krok 02, zadanie #7) → ``ExtractedDocument``: tekst i elementy z pozycją,
- ``VisualAnalyzer`` (krok 03, zadania #10/#11) → ``VisualAnalysis``: elementy wizualne.

Etapy 05–09 (zadania #6, #8) działają na słownikach; orkiestrator przekazuje im treść
w formacie ``ExtractedDocument.as_step_payload()``, a wynik normalizuje do ``FormDefinition``.

Jak podłączyć nowy etap: zaimplementuj protokół i zwróć go w ``stages.default_stages()``.
Współrzędne: ``BoundingBox`` z #17 (ułamki strony 0..1, początek w lewym górnym rogu).
"""

from typing import Annotated, Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from backend.shared.schemas.quality import BoundingBox, Fraction, Issue, SourceChannel
from backend.worker.document import IncomingDocument


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TextElement(ContractModel):
    """Fragment tekstu dokumentu (linia, komórka tabeli, pole AcroForm, słowo OCR)."""

    element_id: str = Field(description="Stabilny identyfikator, np. txt-12 lub acro-3")
    page: Annotated[int, Field(ge=1)]
    text: str
    bbox: BoundingBox | None = None
    channel: SourceChannel = SourceChannel.TEXT
    confidence: Fraction | None = Field(
        default=None, description="Pewność odczytu (OCR); None dla warstwy tekstowej"
    )


class ExtractedDocument(ContractModel):
    """Wynik ekstrakcji tekstu (krok 02)."""

    page_count: Annotated[int, Field(ge=1)]
    elements: list[TextElement]
    title: str | None = Field(default=None, description="Tytuł wniosku, jeśli rozpoznany")
    issues: list[Issue] = Field(default_factory=list, description="Np. słaba jakość skanu")

    @property
    def text(self) -> str:
        return "\n".join(e.text for e in self.elements if e.text.strip())

    def as_step_payload(self) -> dict[str, Any]:
        """Format wejścia etapów 05–09: ``{"title", "text", "elements": [...]}``."""
        payload: dict[str, Any] = {
            "text": self.text,
            "elements": [
                {"element_id": e.element_id, "page": e.page, "text": e.text} for e in self.elements
            ],
        }
        if self.title:
            payload["title"] = self.title
        return payload


VisualKind = Literal[
    "char_boxes",
    "checkbox",
    "checkbox_group",
    "input_line",
    "section_frame",
    "signature",
    "office_only",
    "other",
]


class VisualElement(ContractModel):
    """Element wykryty na obrazie strony (krok 03): OpenCV (``cv-*``) lub VLM (``vlm-*``)."""

    element_id: str
    page: Annotated[int, Field(ge=1)]
    kind: VisualKind
    bbox: BoundingBox
    label: str | None = None
    cells: Annotated[int, Field(ge=1)] | None = Field(default=None, description="Liczba kratek")
    confidence: Fraction | None = None


class VisualAnalysis(ContractModel):
    """Wynik kanału wizualnego (krok 03)."""

    elements: list[VisualElement]
    issues: list[Issue] = Field(default_factory=list)


class TextExtractor(Protocol):
    name: str

    async def extract(self, document: IncomingDocument) -> ExtractedDocument: ...


class VisualAnalyzer(Protocol):
    name: str

    async def analyze(self, document: IncomingDocument) -> VisualAnalysis: ...
