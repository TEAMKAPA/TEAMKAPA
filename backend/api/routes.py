"""Endpointy analizy formularzy: /api/v1/forms/analyze, /api/v1/forms/batch, /api/v1/jobs/{id}."""

from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Path, Query, Request, UploadFile, status
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
from backend.api.schemas import BatchAccepted, JobAccepted, JobView, RejectedFile
from backend.api.security import Subject
from backend.shared.config import ApiSettings, get_api_settings
from backend.shared.jobs import JOB_ID_PATTERN, DocumentRef, JobErrorCode, JobSnapshot, JobStatus
from backend.shared.schemas.quality import AnalysisResult

router = APIRouter(prefix="/api/v1")

RETRY_AFTER_SECONDS = 5

_FILE_ERROR_STATUS = {
    FileErrorCode.FILE_TOO_LARGE: status.HTTP_413_CONTENT_TOO_LARGE,
    FileErrorCode.EXTENSION_NOT_ALLOWED: status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
    FileErrorCode.MIME_MISMATCH: status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
}
_JOB_ERROR_STATUS = {
    JobErrorCode.DOCUMENT_UNREADABLE: status.HTTP_422_UNPROCESSABLE_CONTENT,
    JobErrorCode.PIPELINE_NOT_IMPLEMENTED: status.HTTP_501_NOT_IMPLEMENTED,
}


def get_job_queue(request: Request) -> JobQueue:
    queue: JobQueue = request.app.state.job_queue
    return queue


def get_mime_detector() -> MimeDetector:
    return libmagic_detector


Settings = Annotated[ApiSettings, Depends(get_api_settings)]
Queue = Annotated[JobQueue, Depends(get_job_queue)]
Detector = Annotated[MimeDetector, Depends(get_mime_detector)]


def _status_url(request: Request, job_id: str) -> str:
    return str(request.url_for("get_job", job_id=job_id).path)


async def _validated(
    upload: UploadFile, settings: ApiSettings, detect_mime: MimeDetector
) -> ValidatedFile:
    # Czytamy najwyżej limit + 1 bajt — więcej nie jest potrzebne do odrzucenia pliku.
    content = await upload.read(settings.max_file_size_bytes + 1)
    return validate_upload(
        upload.filename, content, max_bytes=settings.max_file_size_bytes, detect_mime=detect_mime
    )


async def _submit(file: ValidatedFile, owner: str, queue: JobQueue) -> DocumentRef:
    document = DocumentRef(
        job_id=uuid4().hex,
        filename=file.filename,
        extension=file.extension,
        mime_type=file.mime_type,
        size_bytes=file.size_bytes,
        sha256=file.sha256,
        owner=owner,
    )
    try:
        await queue.submit(document, file.content)
    except QueueUnavailableError as exc:
        raise ApiError(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "QUEUE_UNAVAILABLE",
            "Kolejka zadań jest chwilowo niedostępna — spróbuj ponownie",
            headers={"Retry-After": str(RETRY_AFTER_SECONDS)},
        ) from exc
    return document


def _file_error(exc: FileValidationError) -> ApiError:
    return ApiError(
        _FILE_ERROR_STATUS.get(exc.code, status.HTTP_400_BAD_REQUEST), exc.code, exc.message
    )


def _accepted(request: Request, document: DocumentRef) -> JobAccepted:
    return JobAccepted(
        job_id=document.job_id,
        filename=document.filename,
        status_url=_status_url(request, document.job_id),
    )


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
        **error_responses(400, 401, 413, 415, 422, 500, 501, 503),
    },
)
async def analyze(
    request: Request,
    file: Annotated[UploadFile, File(description="PDF, DOC, DOCX, ODS, XLSX, JPG, JPEG lub PNG")],
    subject: Subject,
    settings: Settings,
    queue: Queue,
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
    document = await _submit(validated, subject, queue)
    del validated  # treść pliku jest już tylko w kolejce

    wait_seconds = settings.analyze_wait_seconds if wait is None else wait
    wait_seconds = min(wait_seconds, settings.analyze_max_wait_seconds)
    snapshot = await queue.wait(document.job_id, wait_seconds) if wait_seconds > 0 else None

    if snapshot is not None and snapshot.status is JobStatus.SUCCEEDED and snapshot.result:
        return snapshot.result
    if snapshot is not None and snapshot.status is JobStatus.FAILED and snapshot.error:
        return error_response(
            _JOB_ERROR_STATUS.get(snapshot.error.code, status.HTTP_500_INTERNAL_SERVER_ERROR),
            snapshot.error.code,
            snapshot.error.message,
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
    detect_mime: Detector,
) -> BatchAccepted:
    """Kolejkuje poprawne pliki (każdy jako osobne zadanie), odrzucone zwraca w ``rejected``."""
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
            rejected.append(RejectedFile(filename=upload.filename, code=exc.code,
                                         message=exc.message))  # fmt: skip

    if not validated:
        raise ApiError(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "BATCH_ALL_REJECTED",
            "Żaden plik nie przeszedł walidacji",
            details=[r.model_dump() for r in rejected],
        )

    jobs = [_accepted(request, await _submit(file, subject, queue)) for file in validated]
    return BatchAccepted(jobs=jobs, rejected=rejected)


@router.get(
    "/jobs/{job_id}",
    tags=["jobs"],
    summary="Stan i wynik zadania",
    response_model=JobView,
    response_model_exclude_none=True,
    responses=error_responses(401, 404, 503),
)
async def get_job(
    job_id: Annotated[str, Path(pattern=JOB_ID_PATTERN)],
    subject: Subject,
    queue: Queue,
) -> JobView:
    """Zwraca status (QUEUED, IN_PROGRESS, SUCCEEDED, FAILED), postęp oraz wynik albo błąd.

    Zadanie innego użytkownika jest nieodróżnialne od nieistniejącego (404).
    """
    try:
        snapshot = await queue.get(job_id)
    except QueueUnavailableError as exc:
        raise ApiError(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "QUEUE_UNAVAILABLE",
            "Kolejka zadań jest chwilowo niedostępna",
            headers={"Retry-After": str(RETRY_AFTER_SECONDS)},
        ) from exc
    if snapshot is None or snapshot.document.owner != subject:
        raise ApiError(status.HTTP_404_NOT_FOUND, "JOB_NOT_FOUND", "Nie znaleziono zadania")
    return _job_view(snapshot)


def _job_view(snapshot: JobSnapshot) -> JobView:
    return JobView(
        job_id=snapshot.job_id,
        status=snapshot.status,
        filename=snapshot.document.filename,
        created_at=snapshot.document.created_at,
        progress=snapshot.progress,
        result=snapshot.result,
        error=snapshot.error,
    )
