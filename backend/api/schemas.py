"""Modele odpowiedzi REST API (kontrakt dla frontendu — także w OpenAPI)."""

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from backend.shared.jobs import JobError, JobId, JobProgress, JobStatus
from backend.shared.schemas.coi_form import FormDefinition
from backend.shared.schemas.quality import AnalysisResult, Issue


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class JobAccepted(ApiModel):
    """Plik przyjęty do analizy; wynik pod ``status_url``."""

    job_id: JobId
    status: JobStatus = JobStatus.QUEUED
    filename: str
    status_url: str


class RejectedFile(ApiModel):
    filename: str | None
    code: str
    message: str


class BatchAccepted(ApiModel):
    batch_id: JobId
    status_url: str
    jobs: list[JobAccepted]
    rejected: list[RejectedFile] = Field(default_factory=list)


class JobSummary(ApiModel):
    """Zadanie bez pełnego wyniku (listy)."""

    job_id: JobId
    status: JobStatus
    filename: str
    batch_id: JobId | None = None
    attempts: int
    created_at: datetime
    finished_at: datetime | None = None
    error: JobError | None = None
    approved_at: datetime | None = Field(
        default=None, description="Kiedy urzędnik zatwierdził formularz (jeśli zatwierdził)"
    )


class JobView(JobSummary):
    """Stan zadania. ``result`` tylko dla SUCCEEDED, ``error`` tylko dla FAILED.

    ``retry_reason`` — przy ponowieniu po błędzie przejściowym (status QUEUED).
    """

    started_at: datetime | None = None
    progress: JobProgress | None = None
    retry_reason: str | None = None
    result: AnalysisResult | None = None


class JobList(ApiModel):
    items: list[JobSummary]
    total: int
    limit: int
    offset: int


class BatchView(ApiModel):
    batch_id: JobId
    created_at: datetime
    total_files: int = Field(description="Liczba plików w żądaniu (przyjętych i odrzuconych)")
    finished: bool = Field(description="Wszystkie przyjęte pliki mają status końcowy")
    counts: dict[JobStatus, int]
    jobs: list[JobSummary]
    rejected: list[RejectedFile]


class ApprovalView(ApiModel):
    job_id: JobId
    approved_by: str
    approved_at: datetime
    form: FormDefinition
    warnings: Annotated[
        list[Issue], Field(description="Ostrzeżenia reguł COI (nie blokują zatwierdzenia)")
    ] = Field(default_factory=list)


class HealthResponse(ApiModel):
    status: str
    version: str
