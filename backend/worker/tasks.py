"""Zadania workera. Kontrakt z API: ``backend.shared.jobs``."""

import hashlib
import logging
import time
from typing import Any

from pydantic import ValidationError

from backend.shared.config import Settings, get_settings
from backend.shared.jobs import DocumentRef, JobErrorCode, JobOutcome, file_key
from backend.worker import pipeline
from backend.worker.document import document_workspace
from backend.worker.progress import ProgressReporter

logger = logging.getLogger(__name__)


async def analyze_document(ctx: dict[str, Any], document: dict[str, Any]) -> dict[str, Any]:
    """Analizuje plik przesłany przez API. Zawsze zwraca ``JobOutcome`` (także przy błędzie).

    Plik jest usuwany z pamięci kolejki po zakończeniu analizy (sukces albo obsłużony błąd).
    Przy awarii workera klucz pliku zostaje do wygaśnięcia TTL, więc ponowienie zadania
    przez arq ma jeszcze dane wejściowe.
    """
    settings: Settings = ctx.get("settings") or get_settings()
    job_id = document.get("job_id", "?")
    started = time.monotonic()
    logger.info(
        "Zadanie %s: start (.%s, %s B)",
        job_id,
        document.get("extension"),
        document.get("size_bytes"),
    )
    outcome = await _analyze(ctx["redis"], document, settings)
    logger.info(
        "Zadanie %s: %s%s w %.2f s",
        job_id,
        outcome.status,
        f" ({outcome.error.code})" if outcome.error else "",
        time.monotonic() - started,
    )
    return outcome.model_dump(mode="json", by_alias=True, exclude_none=True)


async def _analyze(redis: Any, raw_document: dict[str, Any], settings: Settings) -> JobOutcome:
    try:
        ref = DocumentRef.model_validate(raw_document)
    except ValidationError:
        logger.exception("Zadanie z nieprawidłowymi metadanymi dokumentu")
        return JobOutcome.failed(JobErrorCode.PIPELINE_ERROR, "Nieprawidłowe dane zadania")

    key = file_key(ref.job_id)
    progress = ProgressReporter(redis, ref.job_id, settings.job_result_ttl_seconds)
    try:
        content: bytes | None = await redis.get(key)
        if content is None:
            return JobOutcome.failed(
                JobErrorCode.FILE_EXPIRED,
                "Plik wygasł w kolejce przed rozpoczęciem analizy — prześlij go ponownie",
            )
        if hashlib.sha256(content).hexdigest() != ref.sha256:
            return JobOutcome.failed(
                JobErrorCode.FILE_CORRUPTED, "Plik w kolejce nie zgadza się z przesłanym"
            )

        with document_workspace(ref, content, settings.temp_dir) as incoming:
            result = await pipeline.run_pipeline(incoming, progress, settings)
        return JobOutcome.succeeded(result)
    except pipeline.PipelineNotImplementedError as exc:
        return JobOutcome.failed(JobErrorCode.PIPELINE_NOT_IMPLEMENTED, str(exc))
    except pipeline.DocumentUnreadableError as exc:
        return JobOutcome.failed(JobErrorCode.DOCUMENT_UNREADABLE, str(exc))
    except Exception:
        logger.exception("Błąd analizy dokumentu %s", ref.job_id)
        return JobOutcome.failed(
            JobErrorCode.PIPELINE_ERROR, "Analiza nie powiodła się z powodu błędu wewnętrznego"
        )
    finally:
        await redis.delete(key)
        await progress.clear()
