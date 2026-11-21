"""Zadania workera. Kontrakt z API: ``backend.shared.jobs``; stan zadań: PostgreSQL.

Obsługa awarii i ponowień:
- błąd przejściowy (``PipelineTemporarilyUnavailableError``) → zadanie wraca do kolejki
  z wykładniczym opóźnieniem, dopóki nie wyczerpie ``MAX_JOB_TRIES``; potem FAILED,
- zamknięcie workera w trakcie analizy (CancelledError) → arq ponawia zadanie, plik zostaje
  w kolejce, następna próba podejmuje zadanie ze statusu IN_PROGRESS,
- twarda awaria workera → arq uruchamia zadanie ponownie po wygaśnięciu blokady; po
  przekroczeniu limitu prób status uzgadnia ``reconcile_stale_jobs``,
- przekroczenie ``JOB_TIMEOUT_SECONDS`` → FAILED (PIPELINE_TIMEOUT), bez ponowienia,
- chwilowa niedostępność PostgreSQL → ponowienie jak przy błędzie przejściowym.
"""

import asyncio
import hashlib
import logging
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from arq import Retry
from pydantic import ValidationError

from backend.shared.config import Settings, get_settings
from backend.shared.db.pool import DB_UNAVAILABLE_ERRORS
from backend.shared.db.repository import JobStore
from backend.shared.jobs import DocumentRef, JobErrorCode, JobOutcome, file_key, progress_key
from backend.shared.reconcile import queue_state, reconcile_job
from backend.worker import pipeline
from backend.worker.document import document_workspace
from backend.worker.progress import ProgressReporter

logger = logging.getLogger(__name__)


def retry_delay(settings: Settings, job_try: int) -> int:
    """Opóźnienie przed kolejną próbą: backoff × 2^(próba-1)."""
    return int(settings.retry_backoff_seconds * 2 ** (job_try - 1))


async def analyze_document(ctx: dict[str, Any], document: dict[str, Any]) -> dict[str, Any]:
    """Analizuje plik przesłany przez API i zapisuje wynik w bazie."""
    try:
        return await _run(ctx, document)
    except DB_UNAVAILABLE_ERRORS as exc:
        settings: Settings = ctx.get("settings") or get_settings()
        job_try: int = ctx.get("job_try", 1)
        if job_try >= settings.max_job_tries:
            raise
        delay = retry_delay(settings, job_try)
        logger.warning("Baza danych niedostępna, ponowienie zadania za %d s", delay)
        raise Retry(defer=delay) from exc


async def _run(ctx: dict[str, Any], document: dict[str, Any]) -> dict[str, Any]:
    settings: Settings = ctx.get("settings") or get_settings()
    store: JobStore = ctx["store"]
    redis = ctx["redis"]
    job_try: int = ctx.get("job_try", 1)
    job_id = document.get("job_id", "?")
    started = time.monotonic()

    try:
        ref = DocumentRef.model_validate(document)
    except ValidationError:
        logger.exception("Zadanie %s z nieprawidłowymi metadanymi dokumentu", job_id)
        return _dump(JobOutcome.failed(JobErrorCode.PIPELINE_ERROR, "Nieprawidłowe dane zadania"))

    attempts = await store.mark_started(ref.job_id)
    if attempts is None:
        # Zadanie usunięte (retencja) albo już zakończone — nie analizujemy ponownie.
        logger.info("Zadanie %s: pominięte (brak w bazie lub zakończone)", ref.job_id)
        await redis.delete(file_key(ref.job_id))
        return _dump(JobOutcome.skipped())

    logger.info(
        "Zadanie %s: start (.%s, %d B, próba %d/%d)",
        ref.job_id,
        ref.extension,
        ref.size_bytes,
        job_try,
        settings.max_job_tries,
    )
    progress = ProgressReporter(redis, ref.job_id, settings.job_result_ttl_seconds)
    try:
        outcome = await _analyze(redis, ref, settings, progress)
    except pipeline.PipelineTemporarilyUnavailableError as exc:
        if job_try < settings.max_job_tries:
            delay = retry_delay(settings, job_try)
            await store.mark_retrying(ref.job_id, f"Ponowienie za {delay} s: {exc}")
            await progress.clear()
            logger.warning("Zadanie %s: błąd przejściowy, ponowienie za %d s", ref.job_id, delay)
            raise Retry(defer=delay) from exc
        outcome = JobOutcome.failed(
            JobErrorCode.PIPELINE_UNAVAILABLE,
            f"Analiza niemożliwa po {job_try} próbach — usługa modelu jest niedostępna",
        )

    if outcome.status == "succeeded" and outcome.result is not None:
        await store.mark_succeeded(ref.job_id, outcome.result)
    elif outcome.error is not None:
        await store.mark_failed(ref.job_id, outcome.error)
    # Zakończenie ostateczne: plik i postęp nie są już potrzebne.
    await redis.delete(file_key(ref.job_id))
    await progress.clear()

    logger.info(
        "Zadanie %s: %s%s w %.2f s",
        ref.job_id,
        outcome.status,
        f" ({outcome.error.code})" if outcome.error else "",
        time.monotonic() - started,
    )
    return _dump(outcome)


async def _analyze(
    redis: Any, ref: DocumentRef, settings: Settings, progress: ProgressReporter
) -> JobOutcome:
    """Zwraca wynik albo błąd ostateczny; błąd przejściowy i anulowanie przepuszcza dalej."""
    content: bytes | None = await redis.get(file_key(ref.job_id))
    if content is None:
        return JobOutcome.failed(
            JobErrorCode.FILE_EXPIRED,
            "Plik wygasł w kolejce przed rozpoczęciem analizy — prześlij go ponownie",
        )
    if hashlib.sha256(content).hexdigest() != ref.sha256:
        return JobOutcome.failed(
            JobErrorCode.FILE_CORRUPTED, "Plik w kolejce nie zgadza się z przesłanym"
        )
    try:
        async with asyncio.timeout(settings.job_timeout_seconds):
            with document_workspace(ref, content, settings.temp_dir) as incoming:
                result = await pipeline.run_pipeline(incoming, progress, settings)
        return JobOutcome.succeeded(result)
    except TimeoutError:
        return JobOutcome.failed(
            JobErrorCode.PIPELINE_TIMEOUT,
            f"Analiza przekroczyła limit czasu ({settings.job_timeout_seconds} s)",
        )
    except pipeline.PipelineNotImplementedError as exc:
        return JobOutcome.failed(JobErrorCode.PIPELINE_NOT_IMPLEMENTED, str(exc))
    except pipeline.DocumentUnreadableError as exc:
        return JobOutcome.failed(JobErrorCode.DOCUMENT_UNREADABLE, str(exc))
    except pipeline.PipelineStageError as exc:
        logger.warning("Zadanie %s: %s", ref.job_id, exc)
        return JobOutcome.failed(JobErrorCode.PIPELINE_STAGE_FAILED, str(exc))
    except pipeline.PipelineTemporarilyUnavailableError:
        raise
    except Exception:
        logger.exception("Błąd analizy dokumentu %s", ref.job_id)
        return JobOutcome.failed(
            JobErrorCode.PIPELINE_ERROR, "Analiza nie powiodła się z powodu błędu wewnętrznego"
        )


def _dump(outcome: JobOutcome) -> dict[str, Any]:
    # W wyniku arq zapisujemy tylko status — pełny wynik jest w bazie.
    return outcome.model_dump(mode="json", include={"status", "error"}, exclude_none=True)


# ---------------------------------------------------------------- zadania cykliczne


async def reconcile_stale_jobs(ctx: dict[str, Any]) -> int:
    """Oznacza jako FAILED zadania, które utknęły (arq je porzucił albo zniknęły z kolejki)."""
    settings: Settings = ctx.get("settings") or get_settings()
    store: JobStore = ctx["store"]
    grace = timedelta(seconds=settings.stale_job_grace_seconds)
    changed = 0
    for job in await store.find_unfinished(datetime.now(UTC) - grace):
        state = await queue_state(ctx["redis"], job.id)
        if await reconcile_job(store, job, state, grace):
            changed += 1
            # Plik porzuconego zadania nie czeka na TTL — usuwamy go od razu.
            await ctx["redis"].delete(file_key(job.id), progress_key(job.id))
            logger.warning("Zadanie %s: uzgodnione jako FAILED (kolejka: %s)", job.id, state)
    return changed


async def purge_old_jobs(ctx: dict[str, Any]) -> int:
    """Usuwa zadania i wygenerowane wyniki starsze niż RESULT_RETENTION_DAYS."""
    settings: Settings = ctx.get("settings") or get_settings()
    store: JobStore = ctx["store"]
    cutoff = datetime.now(UTC) - timedelta(days=settings.result_retention_days)
    deleted = await store.purge_created_before(cutoff)
    if deleted:
        logger.info("Retencja: usunięto %d zadań starszych niż %s", deleted, cutoff.date())
    return deleted
