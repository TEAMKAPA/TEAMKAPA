"""Narzędzia testów pipeline'u: reprezentatywny wniosek, testowe kanały odczytu i klienci LLM.

Ekstrakcja tekstu (#7) i kanał wizualny (#10/#11) nie są jeszcze scalone, dlatego testy
podstawiają implementacje zgodne z kontraktami z ``backend.worker.pipeline.contracts``.
"""

import asyncio
import hashlib
from pathlib import Path
from typing import Any

from backend.shared.config import Settings
from backend.shared.jobs import DocumentRef
from backend.shared.schemas.quality import BoundingBox
from backend.worker.clients.llm_client import LLMClient, LLMConfig
from backend.worker.document import IncomingDocument
from backend.worker.pipeline.contracts import (
    ExtractedDocument,
    TextElement,
    VisualAnalysis,
    VisualElement,
)
from backend.worker.pipeline.errors import PipelineTemporarilyUnavailableError
from backend.worker.pipeline.llm import LLMSession
from backend.worker.pipeline.stages import PipelineStages

#: Reprezentatywny wniosek: dane wnioskodawcy, adres, kontakt, podmiot, zgody, pola urzędu.
REPRESENTATIVE_FORM = [
    "Wniosek o wydanie dowodu osobistego",
    "Urząd Miasta Kraków, Wydział Spraw Obywatelskich",
    "Dane wnioskodawcy",
    "Imię",
    "Nazwisko",
    "Numer PESEL",
    "Data urodzenia",
    "Adres zamieszkania",
    "Miejscowość",
    "Kod pocztowy",
    "Ulica",
    "Numer telefonu",
    "Adres e-mail",
    "Wnoszę w imieniu: własnym / innej osoby",
    "Oświadczam, że dane są zgodne z prawdą",
    "Wyrażam zgodę na przetwarzanie danych osobowych",
    "Podpis wnioskodawcy",
    "Wypełnia urząd",
]


def line_bbox(index: int, total: int) -> BoundingBox:
    top = 0.05 + 0.9 * index / total
    return BoundingBox(x0=0.1, y0=top, x1=0.9, y1=top + 0.9 / total * 0.8)


def representative_document(lines: list[str] = REPRESENTATIVE_FORM) -> ExtractedDocument:
    return ExtractedDocument(
        page_count=1,
        title=lines[0],
        elements=[
            TextElement(element_id=f"txt-{i + 1}", page=1, text=line, bbox=line_bbox(i, len(lines)))
            for i, line in enumerate(lines)
        ],
    )


class FakeTextExtractor:
    name = "test-ekstraktor"

    def __init__(self, document: ExtractedDocument | None = None, error: Exception | None = None):
        self.document = document or representative_document()
        self.error = error
        self.calls = 0

    async def extract(self, document: IncomingDocument) -> ExtractedDocument:
        self.calls += 1
        await asyncio.sleep(0)
        if self.error is not None:
            raise self.error
        return self.document


class FakeVisualAnalyzer:
    name = "test-wizja"

    def __init__(self, mode: str = "overlap", delay: float = 0.0) -> None:
        self.mode = mode
        self.delay = delay
        self.cancelled = False

    async def analyze(self, document: IncomingDocument) -> VisualAnalysis:
        try:
            await asyncio.sleep(self.delay)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        if self.mode == "fail":
            raise RuntimeError("OpenCV: błąd")
        if self.mode == "transient":
            raise PipelineTemporarilyUnavailableError("serwer VLM niedostępny")
        total = len(REPRESENTATIVE_FORM)
        return VisualAnalysis(
            elements=[
                VisualElement(element_id=f"cv-{i}", page=1, kind="input_line",
                              bbox=line_bbox(i, total))
                for i in range(total)
            ]
        )  # fmt: skip


def offline_llm() -> LLMSession:
    """Deterministyczne odpowiedzi etapów #8 (jak tryb demo, ale bez flagi demo)."""
    return LLMSession(LLMClient(LLMConfig(offline=True)))


def stages(
    extractor: FakeTextExtractor | None = None,
    visual: FakeVisualAnalyzer | None = None,
    llm_factory: Any = offline_llm,
) -> PipelineStages:
    return PipelineStages(
        text_extractor=extractor if extractor is not None else FakeTextExtractor(),
        visual_analyzer=visual,
        llm_session_factory=llm_factory,
    )


class RecordingStore:
    """Magazyn klucz-wartość dla ProgressReporter, zapamiętujący kolejne etapy."""

    def __init__(self) -> None:
        self.values: list[str] = []

    async def set(self, name: str, value: str, ex: int | None = None) -> bool:
        self.values.append(value)
        return True

    async def delete(self, *names: str) -> int:
        return len(names)


def incoming(tmp_path: Path, content: bytes = b"%PDF-1.4 test") -> IncomingDocument:
    path = tmp_path / "document.pdf"
    path.write_bytes(content)
    ref = DocumentRef(
        job_id="b" * 32,
        filename="wniosek.pdf",
        extension="pdf",
        mime_type="application/pdf",
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        owner="urzednik",
    )
    return IncomingDocument(ref=ref, content=content, path=path)


def settings(**overrides: Any) -> Settings:
    return Settings().model_copy(update=overrides)
