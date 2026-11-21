"""Endpointy REST API: analiza, zadania, paczki i zatwierdzanie formularzy."""

import asyncio
import logging
import time
from datetime import timedelta
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Body, Depends, File, Path, Query, Request, UploadFile, status
from fastapi.responses import JSONResponse

from backend.api.errors import ApiError, error_response, error_responses
from backend.api.file_validators import (
    FileErrorCode,
    FileValidationError,
    MimeDetector,
    ValidatedFile,
    libmagic_detector,
    validate_upload,
)
from backend.api.job_queue import JobQueue, QueueUnavailableError
from backend.api.schemas import (
    ApprovalView,
    BatchAccepted,
    BatchView,
    JobAccepted,
    JobList,
    JobSummary,
    JobView,
    RejectedFile,
)
from backend.api.security import Subject
from backend.shared.config import ApiSettings, get_api_settings
from backend.shared.db.repository import ApprovalRecord, JobRecord, JobStore, RejectedEntry
from backend.shared.jobs import JOB_ID_PATTERN, DocumentRef, JobErrorCode, JobStatus
from backend.shared.reconcile import reconcile_job
from backend.shared.schemas.coi_form import FormDefinition
from backend.shared.schemas.quality import AnalysisResult, Severity
from backend.shared.validators.form_rules import validate_form_rules

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1")

RETRY_AFTER_SECONDS = 5
_WAIT_POLL_SECONDS = 0.5

_FILE_ERROR_STATUS = {
    FileErrorCode.FILE_TOO_LARGE: status.HTTP_413_CONTENT_TOO_LARGE,
    FileErrorCode.EXTENSION_NOT_ALLOWED: status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
    FileErrorCode.MIME_MISMATCH: status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
}
_JOB_ERROR_STATUS = {
    JobErrorCode.DOCUMENT_UNREADABLE: status.HTTP_422_UNPROCESSABLE_CONTENT,
    JobErrorCode.PIPELINE_NOT_IMPLEMENTED: status.HTTP_501_NOT_IMPLEMENTED,
    JobErrorCode.PIPELINE_UNAVAILABLE: status.HTTP_503_SERVICE_UNAVAILABLE,
    JobErrorCode.PIPELINE_TIMEOUT: status.HTTP_504_GATEWAY_TIMEOUT,
}


# ---------------------------------------------------------------- zależności


def get_job_queue(request: Request) -> JobQueue:
    queue: JobQueue = request.app.state.job_queue
    return queue


def get_job_store(request: Request) -> JobStore:
    store: JobStore = request.app.state.job_store
    return store


def get_mime_detector() -> MimeDetector:
    return libmagic_detector


Settings = Annotated[ApiSettings, Depends(get_api_settings)]
Queue = Annotated[JobQueue, Depends(get_job_queue)]
Store = Annotated[JobStore, Depends(get_job_store)]
Detector = Annotated[MimeDetector, Depends(get_mime_detector)]
JobIdPath = Annotated[str, Path(pattern=JOB_ID_PATTERN)]


# ---------------------------------------------------------------- pomocnicze


def _job_url(request: Request, job_id: str) -> str:
    return str(request.url_for("get_job", job_id=job_id).path)


def _queue_unavailable() -> ApiError:
    return ApiError(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "QUEUE_UNAVAILABLE",
        "Kolejka zadań jest chwilowo niedostępna — spróbuj ponownie",
        headers={"Retry-After": str(RETRY_AFTER_SECONDS)},
    )


def _not_found() -> ApiError:
    return ApiError(status.HTTP_404_NOT_FOUND, "JOB_NOT_FOUND", "Nie znaleziono zadania")


async def _validated(
    upload: UploadFile, settings: ApiSettings, detect_mime: MimeDetector
) -> ValidatedFile:
    # Czytamy najwyżej limit + 1 bajt — więcej nie jest potrzebne do odrzucenia pliku.
    content = await upload.read(settings.max_file_size_bytes + 1)
    return validate_upload(
        upload.filename, content, max_bytes=settings.max_file_size_bytes, detect_mime=detect_mime
    )


async def _submit(
    file: ValidatedFile, owner: str, queue: JobQueue, store: JobStore, batch_id: str | None = None
) -> DocumentRef:
    """Zapisuje zadanie w bazie, potem przekazuje plik do kolejki (bez kolejki — bez zadania)."""
    document = DocumentRef(
        job_id=uuid4().hex,
        filename=file.filename,
        extension=file.extension,
        mime_type=file.mime_type,
        size_bytes=file.size_bytes,
        sha256=file.sha256,
        owner=owner,
    )
    await store.create_job(document, batch_id)
    try:
        await queue.submit(document, file.content)
    except QueueUnavailableError:
        await store.delete_job(document.job_id)
        raise
    return document


def _file_error(exc: FileValidationError) -> ApiError:
    return ApiError(
        _FILE_ERROR_STATUS.get(exc.code, status.HTTP_400_BAD_REQUEST), exc.code, exc.message
    )


def _accepted(request: Request, document: DocumentRef) -> JobAccepted:
    return JobAccepted(
        job_id=document.job_id,
        filename=document.filename,
        status_url=_job_url(request, document.job_id),
    )


def _summary(job: JobRecord) -> JobSummary:
    return JobSummary(
        job_id=job.id,
        status=job.status,
        filename=job.filename,
        batch_id=job.batch_id,
        attempts=job.attempts,
        created_at=job.created_at,
        finished_at=job.finished_at,
        error=job.error,
        approved_at=job.approved_at,
    )


async def _wait_finished(store: JobStore, job_id: str, seconds: float) -> JobRecord | None:
    deadline = time.monotonic() + seconds
    while True:
        job = await store.get_job(job_id, include_result=False)
        if job is None or job.status.finished or time.monotonic() >= deadline:
            break
        await asyncio.sleep(min(_WAIT_POLL_SECONDS, max(0.0, deadline - time.monotonic())))
    if job is not None and job.status is JobStatus.SUCCEEDED:
        return await store.get_job(job_id)
    return job


def _approval_view(record: ApprovalRecord) -> ApprovalView:
    warnings = [i for i in validate_form_rules(record.form) if i.severity is not Severity.ERROR]
    return ApprovalView(
        job_id=record.job_id or "",
        approved_by=record.owner,
        approved_at=record.approved_at,
        form=record.form,
        warnings=warnings,
    )


# ---------------------------------------------------------------- analiza


@router.post(
    "/forms/analyze",
    tags=["forms"],
    summary="Analiza jednego formularza",
    response_model=AnalysisResult,
    response_model_exclude_none=True,
    responses={
        202: {
            "model": JobAccepted,
            "description": "Analiza trwa dłużej niż czas oczekiwania — wynik pod status_url",
        },
        **error_responses(400, 401, 413, 415, 422, 500, 501, 503, 504),
    },
)
async def analyze(
    request: Request,
    file: Annotated[UploadFile, File(description="PDF, DOC, DOCX, ODS, XLSX, JPG, JPEG lub PNG")],
    subject: Subject,
    settings: Settings,
    queue: Queue,
    store: Store,
    detect_mime: Detector,
    wait: Annotated[
        int | None,
        Query(ge=0, description="Ile sekund czekać na wynik (0 = od razu 202 z job_id)."),
    ] = None,
) -> AnalysisResult | JSONResponse:
    """Przyjmuje plik i zwraca konfigurację e-formularza COI z raportem jakości.

    Gdy analiza nie skończy się w czasie oczekiwania (``wait``, domyślnie
    ``ANALYZE_WAIT_SECONDS``), zwraca **202** z ``job_id`` — wynik pobierzesz z
    ``GET /api/v1/jobs/{job_id}`` (nagłówki ``Location`` i ``Retry-After``).
    """
    try:
        validated = await _validated(file, settings, detect_mime)
    except FileValidationError as exc:
        raise _file_error(exc) from exc
    try:
        document = await _submit(validated, subject, queue, store)
    except QueueUnavailableError as exc:
        raise _queue_unavailable() from exc
    del validated  # treść pliku jest już tylko w kolejce

    wait_seconds = settings.analyze_wait_seconds if wait is None else wait
    wait_seconds = min(wait_seconds, settings.analyze_max_wait_seconds)
    job = await _wait_finished(store, document.job_id, wait_seconds) if wait_seconds else None

    if job is not None and job.status is JobStatus.SUCCEEDED and job.result is not None:
        return job.result
    if job is not None and job.status is JobStatus.FAILED and job.error is not None:
        return error_response(
            _JOB_ERROR_STATUS.get(job.error.code, status.HTTP_500_INTERNAL_SERVER_ERROR),
            job.error.code,
            job.error.message,
            {"job_id": document.job_id},
        )
    accepted = _accepted(request, document)
    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content=accepted.model_dump(mode="json"),
        headers={"Location": accepted.status_url, "Retry-After": str(RETRY_AFTER_SECONDS)},
    )


@router.post(
    "/forms/batch",
    tags=["forms"],
    summary="Analiza wsadowa wielu formularzy",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=BatchAccepted,
    responses=error_responses(400, 401, 413, 422, 503),
)
async def batch(
    request: Request,
    files: Annotated[list[UploadFile], File(description="Pliki do analizy")],
    subject: Subject,
    settings: Settings,
    queue: Queue,
    store: Store,
    detect_mime: Detector,
) -> BatchAccepted:
    """Kolejkuje poprawne pliki (każdy jako osobne zadanie), odrzucone zwraca w ``rejected``.

    Stan całej paczki: ``GET /api/v1/batches/{batch_id}``.
    """
    if len(files) > settings.max_batch_files:
        raise ApiError(
            status.HTTP_400_BAD_REQUEST,
            "TOO_MANY_FILES",
            f"Za dużo plików w paczce: {len(files)} (maksymalnie {settings.max_batch_files})",
        )

    validated: list[ValidatedFile] = []
    rejected: list[RejectedFile] = []
    for upload in files:
        try:
            validated.append(await _validated(upload, settings, detect_mime))
        except FileValidationError as exc:
            rejected.append(
                RejectedFile(filename=upload.filename, code=exc.code, message=exc.message)
            )

    if not validated:
        raise ApiError(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "BATCH_ALL_REJECTED",
            "Żaden plik nie przeszedł walidacji",
            details=[r.model_dump() for r in rejected],
        )

    batch_id = uuid4().hex
    await store.create_batch(
        batch_id, subject, len(files), [RejectedEntry(**r.model_dump()) for r in rejected]
    )
    jobs: list[JobAccepted] = []
    queue_rejected: list[RejectedFile] = []
    for file in validated:
        try:
            jobs.append(_accepted(request, await _submit(file, subject, queue, store, batch_id)))
        except QueueUnavailableError as exc:
            if not jobs:  # kolejka niedostępna od początku — nie tworzymy pustej paczki
                await store.delete_batch(batch_id)
                raise _queue_unavailable() from exc
            queue_rejected.append(
                RejectedFile(
                    filename=file.filename,
                    code="QUEUE_UNAVAILABLE",
                    message="Kolejka zadań była chwilowo niedostępna — prześlij plik ponownie",
                )
            )
    if queue_rejected:
        rejected += queue_rejected
        await store.update_batch_rejected(
            batch_id, [RejectedEntry(**r.model_dump()) for r in rejected]
        )

    return BatchAccepted(
        batch_id=batch_id,
        status_url=str(request.url_for("get_batch", batch_id=batch_id).path),
        jobs=jobs,
        rejected=rejected,
    )


# ---------------------------------------------------------------- zadania i paczki


@router.get(
    "/jobs",
    tags=["jobs"],
    summary="Lista moich zadań",
    response_model=JobList,
    response_model_exclude_none=True,
    responses=error_responses(401),
)
async def list_jobs(
    subject: Subject,
    store: Store,
    status_filter: Annotated[JobStatus | None, Query(alias="status")] = None,
    batch_id: Annotated[str | None, Query(pattern=JOB_ID_PATTERN)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> JobList:
    """Zadania zalogowanego użytkownika, od najnowszych (bez pełnych wyników)."""
    jobs, total = await store.list_jobs(
        subject, status=status_filter, batch_id=batch_id, limit=limit, offset=offset
    )
    return JobList(items=[_summary(j) for j in jobs], total=total, limit=limit, offset=offset)


@router.get(
    "/jobs/{job_id}",
    tags=["jobs"],
    summary="Stan i wynik zadania",
    response_model=JobView,
    response_model_exclude_none=True,
    responses=error_responses(401, 404),
)
async def get_job(
    job_id: JobIdPath, subject: Subject, settings: Settings, queue: Queue, store: Store
) -> JobView:
    """Status (QUEUED, IN_PROGRESS, SUCCEEDED, FAILED), postęp, liczba prób, wynik albo błąd.

    Zadanie innego użytkownika jest nieodróżnialne od nieistniejącego (404).
    """
    job = await store.get_job(job_id, subject)
    if job is None:
        raise _not_found()
    progress = None
    if not job.status.finished:
        try:
            if await reconcile_job(
                store,
                job,
                await queue.state(job_id),
                timedelta(seconds=settings.stale_job_grace_seconds),
            ):
                job = await store.get_job(job_id, subject) or job
            elif job.status is JobStatus.IN_PROGRESS:
                progress = await queue.progress(job_id)
        except QueueUnavailableError:
            logger.warning("Kolejka niedostępna — zwracam stan zadania %s z bazy", job_id)
    return JobView(
        **_summary(job).model_dump(),
        started_at=job.started_at,
        progress=progress,
        retry_reason=job.last_error_message if job.status is JobStatus.QUEUED else None,
        result=job.result,
    )


@router.get(
    "/batches/{batch_id}",
    tags=["jobs"],
    summary="Stan paczki plików",
    response_model=BatchView,
    response_model_exclude_none=True,
    responses=error_responses(401, 404),
)
async def get_batch(
    batch_id: Annotated[str, Path(pattern=JOB_ID_PATTERN)], subject: Subject, store: Store
) -> BatchView:
    batch_record = await store.get_batch(batch_id, subject)
    if batch_record is None:
        raise ApiError(status.HTTP_404_NOT_FOUND, "BATCH_NOT_FOUND", "Nie znaleziono paczki")
    jobs, _ = await store.list_jobs(subject, batch_id=batch_id, limit=1000)
    counts = dict.fromkeys(JobStatus, 0)
    for job in jobs:
        counts[job.status] += 1
    return BatchView(
        batch_id=batch_record.id,
        created_at=batch_record.created_at,
        total_files=batch_record.total_files,
        finished=all(j.status.finished for j in jobs),
        counts=counts,
        jobs=[_summary(j) for j in jobs],
        rejected=[RejectedFile(**r.model_dump()) for r in batch_record.rejected],
    )


# ---------------------------------------------------------------- zatwierdzanie


@router.put(
    "/jobs/{job_id}/approval",
    tags=["approval"],
    summary="Zatwierdzenie (poprawionego) formularza",
    response_model=ApprovalView,
    response_model_exclude_none=True,
    responses=error_responses(401, 404, 409, 422),
)
async def approve(
    job_id: JobIdPath,
    form: Annotated[FormDefinition, Body(description="Plik importu COI po korekcie urzędnika")],
    subject: Subject,
    store: Store,
) -> ApprovalView:
    """Zapisuje formularz zatwierdzony przez urzędnika (ponowne wywołanie nadpisuje).

    Formularz musi przejść walidację modelami COI i regułami technicznymi — problemy
    poziomu ERROR blokują zapis (422 ``FORM_RULES_VIOLATED``), ostrzeżenia są zwracane.
    """
    job = await store.get_job(job_id, subject, include_result=False)
    if job is None:
        raise _not_found()
    if job.status is not JobStatus.SUCCEEDED:
        raise ApiError(
            status.HTTP_409_CONFLICT,
            "JOB_NOT_SUCCEEDED",
            f"Można zatwierdzić tylko zakończoną analizę (status: {job.status})",
        )
    issues = validate_form_rules(form)
    errors = [i for i in issues if i.severity is Severity.ERROR]
    if errors:
        raise ApiError(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "FORM_RULES_VIOLATED",
            f"Formularz łamie reguły COI ({len(errors)}) — popraw je przed zatwierdzeniem",
            details=[i.model_dump(mode="json", exclude_none=True) for i in errors],
        )
    return _approval_view(await store.save_approval(job_id, subject, job.sha256, form))


@router.get(
    "/jobs/{job_id}/approval",
    tags=["approval"],
    summary="Zatwierdzony formularz",
    response_model=ApprovalView,
    response_model_exclude_none=True,
    responses=error_responses(401, 404),
)
async def get_approval(job_id: JobIdPath, subject: Subject, store: Store) -> ApprovalView:
    record = await store.get_approval(job_id, subject)
    if record is None:
        raise ApiError(
            status.HTTP_404_NOT_FOUND, "APPROVAL_NOT_FOUND", "Formularz nie został zatwierdzony"
        )
    return _approval_view(record)
