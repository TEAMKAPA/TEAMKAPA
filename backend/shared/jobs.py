"""Kontrakt kolejki zadań między API a workerem.

Źródłem prawdy o zadaniu (status, próby, wynik, błąd) jest PostgreSQL (``backend.shared.db``).
Valkey służy wyłącznie jako kolejka, tymczasowy nośnik pliku i bieżący postęp.

Przepływ pliku (plik nigdy nie trafia na dysk ani do bazy):
1. API zapisuje zadanie w bazie (QUEUED), a bajty pliku w pamięci Valkey pod kluczem
   ``coi:file:{job_id}`` z TTL (``JOB_QUEUE_TTL_SECONDS``); Valkey działa bez zapisu na dysk.
2. API kolejkuje zadanie ``analyze_document`` z samymi metadanymi (``DocumentRef``) —
   argumenty zadania arq są zapisywane także w wyniku, więc bajty pliku nie mogą tam trafić.
3. Worker oznacza zadanie IN_PROGRESS, analizuje plik i zapisuje wynik w bazie. Plik usuwa po
   ostatecznym zakończeniu (sukces lub błąd); przy ponowieniu plik zostaje do kolejnej próby,
   najdłużej do wygaśnięcia TTL.
4. Postęp analizy worker zapisuje pod ``coi:progress:{job_id}``.

Serializacja zadań i wyników: msgpack (zamiast domyślnego pickle — brak wykonywania kodu
przy deserializacji danych z kolejki).
"""

from datetime import UTC, datetime
from enum import StrEnum
from functools import partial
from typing import Annotated, Any, Literal

import msgpack
from pydantic import BaseModel, ConfigDict, Field

from backend.shared.schemas.quality import AnalysisResult

ANALYZE_TASK = "analyze_document"

FILE_KEY_PREFIX = "coi:file:"
PROGRESS_KEY_PREFIX = "coi:progress:"

JOB_ID_PATTERN = r"^[0-9a-f]{32}$"
JobId = Annotated[str, Field(pattern=JOB_ID_PATTERN, description="Identyfikator (hex)")]
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


def file_key(job_id: str) -> str:
    return FILE_KEY_PREFIX + job_id


def progress_key(job_id: str) -> str:
    return PROGRESS_KEY_PREFIX + job_id


def serialize(obj: Any) -> bytes:
    packed: bytes = msgpack.packb(obj, use_bin_type=True)
    return packed


deserialize = partial(msgpack.unpackb, raw=False)


class JobModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DocumentRef(JobModel):
    """Metadane przesłanego dokumentu — argument zadania (bez treści pliku)."""

    job_id: JobId
    filename: str
    extension: str
    mime_type: str
    size_bytes: Annotated[int, Field(ge=1)]
    sha256: Sha256
    owner: str = Field(description="Podmiot z tokenu JWT (claim sub), właściciel zadania")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class JobProgress(JobModel):
    """Postęp analizy raportowany przez worker (do wyświetlenia przy długich analizach)."""

    stage: str = Field(description="Techniczna nazwa etapu, np. text_extraction")
    step: Annotated[int, Field(ge=0)]
    total_steps: Annotated[int, Field(ge=1)]
    message: str | None = Field(default=None, description="Opis etapu dla użytkownika")


class JobErrorCode(StrEnum):
    FILE_EXPIRED = "FILE_EXPIRED"  # plik wygasł w kolejce, zanim worker go pobrał
    FILE_CORRUPTED = "FILE_CORRUPTED"  # suma kontrolna nie zgadza się z przesłaną
    DOCUMENT_UNREADABLE = "DOCUMENT_UNREADABLE"  # nie da się odczytać treści dokumentu
    PIPELINE_NOT_IMPLEMENTED = "PIPELINE_NOT_IMPLEMENTED"
    PIPELINE_UNAVAILABLE = "PIPELINE_UNAVAILABLE"  # błąd przejściowy po wyczerpaniu prób
    PIPELINE_TIMEOUT = "PIPELINE_TIMEOUT"  # przekroczony JOB_TIMEOUT_SECONDS
    PIPELINE_STAGE_FAILED = "PIPELINE_STAGE_FAILED"  # etap zawiódł (nazwa etapu w komunikacie)
    PIPELINE_ERROR = "PIPELINE_ERROR"  # nieoczekiwany błąd analizy
    JOB_FAILED = "JOB_FAILED"  # kolejka przerwała zadanie (np. limit prób po awariach workera)
    JOB_LOST = "JOB_LOST"  # zadanie zniknęło z kolejki (np. restart Valkey)


class JobError(JobModel):
    code: JobErrorCode
    message: str


class JobOutcome(JobModel):
    """Wartość zwracana przez zadanie ``analyze_document`` (zawsze — także przy błędzie)."""

    status: Literal["succeeded", "failed", "skipped"]
    result: AnalysisResult | None = None
    error: JobError | None = None

    @classmethod
    def succeeded(cls, result: AnalysisResult) -> JobOutcome:
        return cls(status="succeeded", result=result)

    @classmethod
    def failed(cls, code: JobErrorCode, message: str) -> JobOutcome:
        return cls(status="failed", error=JobError(code=code, message=message))

    @classmethod
    def skipped(cls) -> JobOutcome:
        """Zadanie nie istnieje już w bazie albo jest zakończone — nic do zrobienia."""
        return cls(status="skipped")


class JobStatus(StrEnum):
    QUEUED = "QUEUED"
    IN_PROGRESS = "IN_PROGRESS"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"

    @property
    def finished(self) -> bool:
        return self in {JobStatus.SUCCEEDED, JobStatus.FAILED}


class QueueState(StrEnum):
    """Stan zadania widziany przez kolejkę arq (do uzgadniania z bazą)."""

    PENDING = "PENDING"  # w kolejce, odroczone (ponowienie) lub w trakcie
    COMPLETE = "COMPLETE"  # arq zapisał wynik (także porażkę, np. limit prób)
    NOT_FOUND = "NOT_FOUND"  # brak śladu w kolejce
