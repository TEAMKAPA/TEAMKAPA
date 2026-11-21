"""Uzgadnianie statusu zadania w bazie ze stanem kolejki arq.

Zadanie może utknąć jako QUEUED/IN_PROGRESS, gdy arq zakończy je bez wywołania naszego kodu
albo bez zapisu wyniku: przekroczony limit prób po awariach workera (arq zapisuje porażkę
i nie uruchamia funkcji), przerwanie przez timeout kolejki, utrata kolejki (restart Valkey
bez trwałości). Uzgadnianie oznacza takie zadania jako FAILED z czytelnym kodem.

Zadania świeże (``updated_at`` młodsze niż ``STALE_JOB_GRACE_SECONDS``) są pomijane, żeby nie
oznaczyć zadania, które właśnie zostało zapisane w bazie, a jeszcze nie trafiło do kolejki.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

from arq.jobs import Job
from arq.jobs import JobStatus as ArqJobStatus

from backend.shared.db.repository import JobRecord, JobStore
from backend.shared.jobs import JobError, JobErrorCode, QueueState, deserialize

_LOST = JobError(
    code=JobErrorCode.JOB_LOST,
    message="Zadanie zniknęło z kolejki (np. po restarcie usług) — prześlij plik ponownie",
)
_ABORTED = JobError(
    code=JobErrorCode.JOB_FAILED,
    message="Analiza została przerwana (awaria workera lub wyczerpany limit prób)",
)


async def queue_state(redis: Any, job_id: str) -> QueueState:
    status = await Job(job_id, redis, _deserializer=deserialize).status()
    if status is ArqJobStatus.complete:
        return QueueState.COMPLETE
    if status is ArqJobStatus.not_found:
        return QueueState.NOT_FOUND
    return QueueState.PENDING


def stale_error(
    job: JobRecord, state: QueueState, now: datetime, grace: timedelta
) -> JobError | None:
    """Błąd do zapisania, jeśli zadanie utknęło; ``None``, gdy nic nie trzeba robić."""
    if job.status.finished or state is QueueState.PENDING:
        return None
    if now - job.updated_at < grace:
        return None
    return _ABORTED if state is QueueState.COMPLETE else _LOST


async def reconcile_job(
    store: JobStore, job: JobRecord, state: QueueState, grace: timedelta
) -> bool:
    """Oznacza zadanie jako FAILED, jeśli utknęło. Zwraca True, gdy zmieniono status."""
    error = stale_error(job, state, datetime.now(UTC), grace)
    if error is None:
        return False
    return await store.mark_failed(job.id, error)
