"""Pipeline analizy dokumentu (etapy 1–10).

Punkt wejścia ``run_pipeline`` wywołuje zadanie ``analyze_document``. Implementacja:
``orchestrator`` (przebieg etapów), ``contracts`` (wejście/wyjście etapów), ``stages``
(rejestr implementacji), ``normalize`` (wynik → plik COI), ``scoring`` (pewność pól).

Kontrakt błędów dla etapów (decyduje o statusie zadania):
- ``DocumentUnreadableError`` — dokument nieczytelny (błąd ostateczny),
- ``PipelineTemporarilyUnavailableError`` — zależność chwilowo niedostępna (serwer
  LLM/VLM, timeout połączenia) → ponowienie do ``MAX_JOB_TRIES``,
- ``PipelineStageError`` — etap zawiódł (komunikat z nazwą etapu trafia do użytkownika),
- ``PipelineNotImplementedError`` — brak wymaganego etapu (501).
"""

from typing import TYPE_CHECKING

from backend.shared.config import Settings
from backend.shared.schemas.quality import AnalysisResult
from backend.worker.document import IncomingDocument
from backend.worker.pipeline.errors import (
    DocumentUnreadableError,
    PipelineNotImplementedError,
    PipelineStageError,
    PipelineTemporarilyUnavailableError,
)
from backend.worker.progress import ProgressReporter

if TYPE_CHECKING:
    from backend.worker.rag.knowledge import KnowledgeBase

__all__ = [
    "DocumentUnreadableError",
    "PipelineNotImplementedError",
    "PipelineStageError",
    "PipelineTemporarilyUnavailableError",
    "run_pipeline",
]


async def run_pipeline(
    document: IncomingDocument,
    progress: ProgressReporter,
    settings: Settings,
    knowledge: KnowledgeBase | None = None,
) -> AnalysisResult:
    """Analizuje dokument i zwraca plik importu COI z raportem jakości.

    ``knowledge`` — baza wiedzy (RAG) workera; bez niej typ rozpoznaje dopasowanie leksykalne.
    """
    # Import leniwy: orkiestrator importuje etapy, a te — moduły tego pakietu.
    from backend.worker.pipeline.orchestrator import run_pipeline as run
    from backend.worker.pipeline.stages import default_stages

    stages = default_stages(settings, knowledge) if knowledge is not None else None
    return await run(document, progress, settings, stages)
