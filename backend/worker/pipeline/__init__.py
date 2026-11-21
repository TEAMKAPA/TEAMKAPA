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

__all__ = [
    "DocumentUnreadableError",
    "PipelineNotImplementedError",
    "PipelineStageError",
    "PipelineTemporarilyUnavailableError",
    "run_pipeline",
]


async def run_pipeline(
    document: IncomingDocument, progress: ProgressReporter, settings: Settings
) -> AnalysisResult:
    """Analizuje dokument i zwraca plik importu COI z raportem jakości."""
    # Import leniwy: orkiestrator importuje etapy, a te — moduły tego pakietu.
    from backend.worker.pipeline.orchestrator import run_pipeline as run

    return await run(document, progress, settings)
