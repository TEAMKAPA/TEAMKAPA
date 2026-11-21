"""Implementacje w pamięci: magazyn zadań (JobStore) i kolejka (JobQueue) do testów jednostkowych.

Zachowanie odwzorowuje warunkowe przejścia statusów z ``PgJobStore`` — te same scenariusze
testy integracyjne sprawdzają na prawdziwym PostgreSQL.
"""

from datetime import UTC, datetime
from typing import Literal

from backend.api.job_queue import QueueUnavailableError
from backend.shared.db.repository import ApprovalRecord, BatchRecord, JobRecord, RejectedEntry
from backend.shared.jobs import (
    DocumentRef,
    JobError,
    JobErrorCode,
    JobProgress,
    JobStatus,
    QueueState,
)
from backend.shared.schemas.coi_form import FormDefinition
from backend.shared.schemas.quality import AnalysisResult, QualityReport
from tests.coi_helpers import sample

_UNFINISHED = {JobStatus.QUEUED, JobStatus.IN_PROGRESS}


def sample_result(sha256: str = "0" * 64) -> AnalysisResult:
    return AnalysisResult(
        form=FormDefinition.model_validate(sample()),
        report=QualityReport(document_sha256=sha256, accuracy_mode="max"),
    )


def _now() -> datetime:
    return datetime.now(UTC)


class FakeJobStore:
    def __init__(self) -> None:
        self.jobs: dict[str, JobRecord] = {}
        self.batches: dict[str, BatchRecord] = {}
        self.approvals: dict[str, ApprovalRecord] = {}
        self.available = True
        self.closed = False

    async def ping(self) -> bool:
        return self.available

    async def close(self) -> None:
        self.closed = True

    async def create_batch(
        self, batch_id: str, owner: str, total_files: int, rejected: list[RejectedEntry]
    ) -> None:
        self.batches[batch_id] = BatchRecord(
            id=batch_id, owner=owner, total_files=total_files, rejected=rejected, created_at=_now()
        )

    async def update_batch_rejected(self, batch_id: str, rejected: list[RejectedEntry]) -> None:
        self.batches[batch_id] = self.batches[batch_id].model_copy(update={"rejected": rejected})

    async def delete_batch(self, batch_id: str) -> None:
        self.batches.pop(batch_id, None)
        for job_id in [j.id for j in self.jobs.values() if j.batch_id == batch_id]:
            del self.jobs[job_id]

    def _check_available(self) -> None:
        if not self.available:
            raise ConnectionRefusedError("postgres down")

    async def create_job(self, document: DocumentRef, batch_id: str | None = None) -> None:
        self._check_available()
        self.jobs[document.job_id] = JobRecord(
            id=document.job_id,
            owner=document.owner,
            batch_id=batch_id,
            filename=document.filename,
            extension=document.extension,
            mime_type=document.mime_type,
            size_bytes=document.size_bytes,
            sha256=document.sha256,
            status=JobStatus.QUEUED,
            attempts=0,
            error=None,
            last_error_message=None,
            result=None,
            created_at=document.created_at,
            started_at=None,
            finished_at=None,
            updated_at=document.created_at,
            approved_at=None,
        )

    async def delete_job(self, job_id: str) -> None:
        self.jobs.pop(job_id, None)

    def _with_approval(self, job: JobRecord) -> JobRecord:
        approval = self.approvals.get(job.id)
        return job.model_copy(update={"approved_at": approval.approved_at if approval else None})

    async def get_job(
        self, job_id: str, owner: str | None = None, *, include_result: bool = True
    ) -> JobRecord | None:
        self._check_available()
        job = self.jobs.get(job_id)
        if job is None or (owner is not None and job.owner != owner):
            return None
        job = self._with_approval(job)
        return job if include_result else job.model_copy(update={"result": None})

    async def list_jobs(
        self,
        owner: str,
        *,
        status: JobStatus | None = None,
        batch_id: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[JobRecord], int]:
        matching = [
            self._with_approval(j).model_copy(update={"result": None})
            for j in self.jobs.values()
            if j.owner == owner
            and (status is None or j.status is status)
            and (batch_id is None or j.batch_id == batch_id)
        ]
        matching.sort(key=lambda j: (j.created_at, j.id), reverse=True)
        return matching[offset : offset + limit], len(matching)

    async def get_batch(self, batch_id: str, owner: str) -> BatchRecord | None:
        batch = self.batches.get(batch_id)
        return batch if batch is not None and batch.owner == owner else None

    def _update(self, job_id: str, **changes: object) -> bool:
        job = self.jobs.get(job_id)
        if job is None or job.status not in _UNFINISHED:
            return False
        self.jobs[job_id] = job.model_copy(update={**changes, "updated_at": _now()})
        return True

    async def mark_started(self, job_id: str) -> int | None:
        self._check_available()
        job = self.jobs.get(job_id)
        if job is None or job.status not in _UNFINISHED:
            return None
        self._update(
            job_id,
            status=JobStatus.IN_PROGRESS,
            attempts=job.attempts + 1,
            started_at=job.started_at or _now(),
        )
        return job.attempts + 1

    async def mark_retrying(self, job_id: str, message: str) -> bool:
        return self._update(job_id, status=JobStatus.QUEUED, last_error_message=message)

    async def mark_succeeded(self, job_id: str, result: AnalysisResult) -> bool:
        return self._update(
            job_id,
            status=JobStatus.SUCCEEDED,
            result=result,
            error=None,
            last_error_message=None,
            finished_at=_now(),
        )

    async def mark_failed(self, job_id: str, error: JobError) -> bool:
        return self._update(
            job_id, status=JobStatus.FAILED, error=error, result=None, finished_at=_now()
        )

    async def find_unfinished(self, updated_before: datetime) -> list[JobRecord]:
        return [
            j
            for j in self.jobs.values()
            if j.status in _UNFINISHED and j.updated_at < updated_before
        ]

    async def purge_created_before(self, cutoff: datetime) -> int:
        old = [j.id for j in self.jobs.values() if j.created_at < cutoff]
        for job_id in old:
            del self.jobs[job_id]
        return len(old)

    async def save_approval(
        self, job_id: str, owner: str, sha256: str, form: FormDefinition
    ) -> ApprovalRecord:
        record = ApprovalRecord(
            job_id=job_id, owner=owner, sha256=sha256, title=form.title, form=form,
            approved_at=_now(),
        )  # fmt: skip
        self.approvals[job_id] = record
        return record

    async def get_approval(self, job_id: str, owner: str) -> ApprovalRecord | None:
        record = self.approvals.get(job_id)
        return record if record is not None and record.owner == owner else None


Behaviour = Literal["succeed", "pending", "in_progress", "fail", "unavailable"]


class FakeJobQueue:
    """Kolejka symulująca workera: po ``submit`` od razu ustawia stan zadania w magazynie."""

    def __init__(
        self,
        store: FakeJobStore,
        behaviour: Behaviour = "succeed",
        error_code: JobErrorCode = JobErrorCode.PIPELINE_NOT_IMPLEMENTED,
    ) -> None:
        self.store = store
        self.behaviour = behaviour
        self.error_code = error_code
        self.files: dict[str, bytes] = {}
        self.states: dict[str, QueueState] = {}
        self.fail_after: int | None = None  # niedostępna po N udanych submit
        self.closed = False

    async def submit(self, document: DocumentRef, content: bytes) -> None:
        if self.behaviour == "unavailable" or (
            self.fail_after is not None and len(self.files) >= self.fail_after
        ):
            raise QueueUnavailableError("valkey down")
        self.files[document.job_id] = content
        self.states[document.job_id] = QueueState.PENDING
        job_id = document.job_id
        match self.behaviour:
            case "succeed":
                await self.store.mark_started(job_id)
                await self.store.mark_succeeded(job_id, sample_result(document.sha256))
                self.states[job_id] = QueueState.COMPLETE
            case "fail":
                await self.store.mark_started(job_id)
                await self.store.mark_failed(
                    job_id, JobError(code=self.error_code, message="Błąd testowy")
                )
                self.states[job_id] = QueueState.COMPLETE
            case "in_progress":
                await self.store.mark_started(job_id)

    async def progress(self, job_id: str) -> JobProgress | None:
        if self.behaviour == "unavailable":
            raise QueueUnavailableError("valkey down")
        return JobProgress(stage="text_extraction", step=2, total_steps=10)

    async def state(self, job_id: str) -> QueueState:
        if self.behaviour == "unavailable":
            raise QueueUnavailableError("valkey down")
        return self.states.get(job_id, QueueState.NOT_FOUND)

    async def ping(self) -> bool:
        return self.behaviour != "unavailable"

    async def close(self) -> None:
        self.closed = True
