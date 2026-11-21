"""Kolejka w pamięci i fabryka klienta do testów endpointów API."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Literal

from fastapi.testclient import TestClient

from backend.api.job_queue import QueueUnavailableError
from backend.api.main import create_app
from backend.api.routes import get_mime_detector
from backend.api.security import issue_token
from backend.shared.config import ApiSettings, get_api_settings
from backend.shared.jobs import (
    DocumentRef,
    JobError,
    JobErrorCode,
    JobProgress,
    JobSnapshot,
    JobStatus,
)
from backend.shared.schemas.coi_form import FormDefinition
from backend.shared.schemas.quality import AnalysisResult, QualityReport
from tests.coi_helpers import sample
from tests.file_samples import sniff_mime

Behaviour = Literal["succeed", "pending", "in_progress", "fail", "unavailable"]


def sample_result(sha256: str = "0" * 64) -> AnalysisResult:
    return AnalysisResult(
        form=FormDefinition.model_validate(sample()),
        report=QualityReport(document_sha256=sha256, accuracy_mode="max"),
    )


class FakeJobQueue:
    """Symuluje kolejkę: zachowanie zadań ustawia ``behaviour``."""

    def __init__(
        self,
        behaviour: Behaviour = "succeed",
        error_code: JobErrorCode = JobErrorCode.PIPELINE_NOT_IMPLEMENTED,
    ) -> None:
        self.behaviour = behaviour
        self.error_code = error_code
        self.files: dict[str, bytes] = {}
        self.snapshots: dict[str, JobSnapshot] = {}
        self.wait_calls: list[float] = []
        self.closed = False

    async def submit(self, document: DocumentRef, content: bytes) -> None:
        if self.behaviour == "unavailable":
            raise QueueUnavailableError("valkey down")
        self.files[document.job_id] = content
        self.snapshots[document.job_id] = self._snapshot(document)

    def _snapshot(self, document: DocumentRef) -> JobSnapshot:
        base = {"job_id": document.job_id, "document": document}
        match self.behaviour:
            case "succeed":
                return JobSnapshot(
                    **base, status=JobStatus.SUCCEEDED, result=sample_result(document.sha256)
                )
            case "fail":
                return JobSnapshot(
                    **base,
                    status=JobStatus.FAILED,
                    error=JobError(code=self.error_code, message="Błąd testowy"),
                )
            case "in_progress":
                return JobSnapshot(
                    **base,
                    status=JobStatus.IN_PROGRESS,
                    progress=JobProgress(stage="text_extraction", step=2, total_steps=10),
                )
            case _:
                return JobSnapshot(**base, status=JobStatus.QUEUED)

    async def get(self, job_id: str) -> JobSnapshot | None:
        if self.behaviour == "unavailable":
            raise QueueUnavailableError("valkey down")
        return self.snapshots.get(job_id)

    async def wait(self, job_id: str, max_seconds: float) -> JobSnapshot | None:
        self.wait_calls.append(max_seconds)
        return self.snapshots.get(job_id)

    async def ping(self) -> bool:
        return self.behaviour != "unavailable"

    async def close(self) -> None:
        self.closed = True

    def add_foreign_job(self, owner: str) -> str:
        job_id = "f" * 32
        document = DocumentRef(
            job_id=job_id,
            filename="obcy.pdf",
            extension="pdf",
            mime_type="application/pdf",
            size_bytes=10,
            sha256="1" * 64,
            owner=owner,
            created_at=datetime.now(UTC),
        )
        self.snapshots[job_id] = JobSnapshot(job_id=job_id, status=JobStatus.QUEUED,
                                             document=document)  # fmt: skip
        return job_id


@contextmanager
def api_client(queue: FakeJobQueue) -> Iterator[TestClient]:
    async def factory(_: ApiSettings) -> FakeJobQueue:
        return queue

    app = create_app(queue_factory=factory)
    app.dependency_overrides[get_mime_detector] = lambda: sniff_mime
    with TestClient(app) as client:
        yield client


def auth(subject: str = "urzednik") -> dict[str, str]:
    return {"Authorization": f"Bearer {issue_token(get_api_settings(), subject)}"}
