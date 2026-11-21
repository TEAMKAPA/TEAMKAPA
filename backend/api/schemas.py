"""Modele odpowiedzi REST API (kontrakt dla frontendu — także w OpenAPI)."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from backend.shared.jobs import JobError, JobId, JobProgress, JobStatus
from backend.shared.schemas.quality import AnalysisResult


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
    jobs: list[JobAccepted]
    rejected: list[RejectedFile] = Field(default_factory=list)


class JobView(ApiModel):
    """Stan zadania. ``result`` tylko dla SUCCEEDED, ``error`` tylko dla FAILED."""

    job_id: JobId
    status: JobStatus
    filename: str
    created_at: datetime
    progress: JobProgress | None = None
    result: AnalysisResult | None = None
    error: JobError | None = None


class HealthResponse(ApiModel):
    status: str
    version: str
