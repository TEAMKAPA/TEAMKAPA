"""Pipeline analizy dokumentu (etapy 1–10).

Punkt wejścia wywoływany przez zadanie ``analyze_document``. Integracja etapów to zadanie #16;
do tego czasu funkcja zgłasza ``PipelineNotImplementedError``, a API zwraca 501 z czytelnym
komunikatem zamiast udawać wynik.
"""

from backend.shared.config import Settings
from backend.shared.schemas.quality import AnalysisResult
from backend.worker.document import IncomingDocument
from backend.worker.progress import ProgressReporter


class PipelineNotImplementedError(RuntimeError):
    """Pipeline nie jest jeszcze zintegrowany (#16)."""


class DocumentUnreadableError(RuntimeError):
    """Treści dokumentu nie da się odczytać (uszkodzony plik, chroniony hasłem itp.)."""


class PipelineTemporarilyUnavailableError(RuntimeError):
    """Błąd przejściowy (np. lokalny serwer modelu niedostępny) — zadanie zostanie ponowione."""


async def run_pipeline(
    document: IncomingDocument, progress: ProgressReporter, settings: Settings
) -> AnalysisResult:
    """Analizuje dokument i zwraca plik importu COI z raportem jakości.

    Kontrakt dla #16: postęp raportuj przez ``progress.update(...)``; dokument nieczytelny
    sygnalizuj ``DocumentUnreadableError``; chwilową niedostępność zależności (serwer LLM/VLM,
    timeout połączenia) — ``PipelineTemporarilyUnavailableError`` (ponowienie do
    ``MAX_JOB_TRIES``); każdy inny wyjątek kończy zadanie błędem ``PIPELINE_ERROR``.
    """
    raise PipelineNotImplementedError(
        "Pipeline analizy nie jest jeszcze zintegrowany (zadanie #16)"
    )
