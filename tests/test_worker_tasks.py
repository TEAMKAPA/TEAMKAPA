"""Zadanie analyze_document: wynik, błędy, ponowienia i awarie (Valkey i baza w pamięci)."""

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from arq import Retry

from backend.shared.config import Settings
from backend.shared.jobs import (
    DocumentRef,
    JobErrorCode,
    JobStatus,
    QueueState,
    deserialize,
    file_key,
    progress_key,
    serialize,
)
from backend.shared.reconcile import stale_error
from backend.shared.schemas.quality import AnalysisResult
from backend.worker import pipeline
from backend.worker.document import IncomingDocument
from backend.worker.progress import ProgressReporter
from backend.worker.tasks import analyze_document, purge_old_jobs, retry_delay
from tests.fakes import FakeJobStore, sample_result
from tests.file_samples import pdf

pytestmark = pytest.mark.anyio

JOB_ID = "a" * 32


class FakeRedis:
    def __init__(self) -> None:
        self.data: dict[str, Any] = {}

    async def get(self, name: str) -> Any:
        return self.data.get(name)

    async def set(self, name: str, value: Any, ex: int | None = None) -> bool:
        self.data[name] = value
        return True

    async def delete(self, *names: str) -> int:
        for name in names:
            self.data.pop(name, None)
        return len(names)


def _ref(content: bytes = pdf()) -> DocumentRef:
    return DocumentRef(
        job_id=JOB_ID,
        filename="wniosek.pdf",
        extension="pdf",
        mime_type="application/pdf",
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        owner="urzednik",
    )


class Env:
    def __init__(self, settings: Settings | None = None) -> None:
        self.redis = FakeRedis()
        self.store = FakeJobStore()
        self.settings = settings or Settings()

    async def add_job(self, content: bytes = pdf(), *, with_file: bool = True) -> DocumentRef:
        ref = _ref(content)
        await self.store.create_job(ref)
        if with_file:
            self.redis.data[file_key(ref.job_id)] = pdf()
        return ref

    async def run(self, ref: DocumentRef | dict[str, Any], job_try: int = 1) -> dict[str, Any]:
        document = ref.model_dump(mode="json") if isinstance(ref, DocumentRef) else ref
        ctx = {"redis": self.redis, "store": self.store, "settings": self.settings,
               "job_try": job_try}  # fmt: skip
        raw = await analyze_document(ctx, document)
        assert deserialize(serialize(raw)) == raw  # wynik przechodzi przez msgpack kolejki
        return raw

    def job(self) -> Any:
        return self.store.jobs[JOB_ID]


def _patch_pipeline(monkeypatch: pytest.MonkeyPatch, behaviour: Any) -> None:
    async def run(
        document: IncomingDocument, progress: ProgressReporter, settings: Settings
    ) -> AnalysisResult:
        result: AnalysisResult = await behaviour(document, progress)
        return result

    monkeypatch.setattr(pipeline, "run_pipeline", run)


# ------------------------------------------------------------------ wynik i błędy ostateczne


async def test_missing_text_extractor_reported_as_not_implemented(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.worker.pipeline import orchestrator
    from backend.worker.pipeline.stages import PipelineStages

    monkeypatch.setattr(
        orchestrator,
        "default_stages",
        lambda _: PipelineStages(None, None, lambda: None),  # type: ignore[arg-type, return-value]
    )
    env = Env()
    raw = await env.run(await env.add_job())
    assert raw["status"] == "failed"
    assert raw["error"]["code"] == "PIPELINE_NOT_IMPLEMENTED"
    assert "ekstrakcji tekstu" in raw["error"]["message"]
    assert env.job().status is JobStatus.FAILED
    assert env.job().error.code is JobErrorCode.PIPELINE_NOT_IMPLEMENTED
    assert env.job().attempts == 1
    assert env.redis.data == {}


async def test_success_stored_in_database_and_cleaned_up(monkeypatch: pytest.MonkeyPatch) -> None:
    env = Env()
    seen: dict[str, Path] = {}

    async def behaviour(document: IncomingDocument, progress: ProgressReporter) -> AnalysisResult:
        seen["path"] = document.path
        assert document.path.read_bytes() == pdf()
        assert document.path.name == "document.pdf"  # nazwa nie pochodzi od użytkownika
        await progress.update("text_extraction", 2, 10, "Odczyt tekstu")
        assert progress_key(JOB_ID) in env.redis.data
        assert env.job().status is JobStatus.IN_PROGRESS
        return sample_result(document.ref.sha256)

    _patch_pipeline(monkeypatch, behaviour)
    raw = await env.run(await env.add_job())

    assert raw == {"status": "succeeded"}  # pełny wynik tylko w bazie, nie w kolejce
    job = env.job()
    assert job.status is JobStatus.SUCCEEDED
    assert job.result is not None
    assert job.result.report.document_sha256 == hashlib.sha256(pdf()).hexdigest()
    assert job.finished_at is not None
    assert env.redis.data == {}
    assert not seen["path"].parent.exists()


async def test_missing_file_reports_expired() -> None:
    env = Env()
    await env.run(await env.add_job(with_file=False))
    assert env.job().error.code is JobErrorCode.FILE_EXPIRED


async def test_checksum_mismatch() -> None:
    env = Env()
    await env.run(await env.add_job(b"inna tresc"))
    assert env.job().error.code is JobErrorCode.FILE_CORRUPTED
    assert env.redis.data == {}


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (
            pipeline.DocumentUnreadableError("Plik chroniony hasłem"),
            JobErrorCode.DOCUMENT_UNREADABLE,
        ),
        (RuntimeError("szczegóły wewnętrzne"), JobErrorCode.PIPELINE_ERROR),
    ],
)
async def test_final_pipeline_errors(
    monkeypatch: pytest.MonkeyPatch, error: Exception, code: JobErrorCode
) -> None:
    async def behaviour(*_: object) -> AnalysisResult:
        raise error

    _patch_pipeline(monkeypatch, behaviour)
    env = Env()
    await env.run(await env.add_job())
    assert env.job().error.code is code
    assert "szczegóły wewnętrzne" not in env.job().error.message  # brak wycieku szczegółów
    assert env.redis.data == {}


async def test_timeout_fails_without_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    async def behaviour(*_: object) -> AnalysisResult:
        await asyncio.sleep(5)
        raise AssertionError("niedostępne")

    _patch_pipeline(monkeypatch, behaviour)
    env = Env(Settings().model_copy(update={"job_timeout_seconds": 0.05}))
    await env.run(await env.add_job())
    assert env.job().error.code is JobErrorCode.PIPELINE_TIMEOUT
    assert env.redis.data == {}


async def test_invalid_job_arguments() -> None:
    raw = await Env().run({"job_id": "zly"})
    assert raw["error"]["code"] == "PIPELINE_ERROR"


# ------------------------------------------------------------------ ponowienia


def test_retry_delay_is_exponential() -> None:
    settings = Settings()
    assert [retry_delay(settings, t) for t in (1, 2, 3)] == [30, 60, 120]


async def test_transient_error_is_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    async def behaviour(*_: object) -> AnalysisResult:
        raise pipeline.PipelineTemporarilyUnavailableError("serwer LLM niedostępny")

    _patch_pipeline(monkeypatch, behaviour)
    env = Env()
    ref = await env.add_job()

    with pytest.raises(Retry) as exc:
        await env.run(ref, job_try=1)
    assert exc.value.defer_score == 30_000
    job = env.job()
    assert job.status is JobStatus.QUEUED
    assert job.attempts == 1
    assert "serwer LLM niedostępny" in (job.last_error_message or "")
    assert file_key(JOB_ID) in env.redis.data  # plik zostaje na kolejną próbę

    with pytest.raises(Retry) as exc:
        await env.run(ref, job_try=2)
    assert exc.value.defer_score == 60_000
    assert env.job().attempts == 2


async def test_transient_error_fails_after_last_try(monkeypatch: pytest.MonkeyPatch) -> None:
    async def behaviour(*_: object) -> AnalysisResult:
        raise pipeline.PipelineTemporarilyUnavailableError("serwer LLM niedostępny")

    _patch_pipeline(monkeypatch, behaviour)
    env = Env()
    await env.run(await env.add_job(), job_try=3)
    assert env.job().status is JobStatus.FAILED
    assert env.job().error.code is JobErrorCode.PIPELINE_UNAVAILABLE
    assert env.redis.data == {}


async def test_transient_error_then_success(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    async def behaviour(document: IncomingDocument, _: ProgressReporter) -> AnalysisResult:
        calls["n"] += 1
        if calls["n"] == 1:
            raise pipeline.PipelineTemporarilyUnavailableError("chwilowa przerwa")
        return sample_result(document.ref.sha256)

    _patch_pipeline(monkeypatch, behaviour)
    env = Env()
    ref = await env.add_job()
    with pytest.raises(Retry):
        await env.run(ref, job_try=1)
    await env.run(ref, job_try=2)
    job = env.job()
    assert job.status is JobStatus.SUCCEEDED
    assert job.attempts == 2
    assert job.last_error_message is None


async def test_worker_shutdown_keeps_file_and_resumes(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    async def behaviour(document: IncomingDocument, _: ProgressReporter) -> AnalysisResult:
        calls["n"] += 1
        if calls["n"] == 1:
            raise asyncio.CancelledError  # zamknięcie workera w trakcie analizy
        return sample_result(document.ref.sha256)

    _patch_pipeline(monkeypatch, behaviour)
    env = Env()
    ref = await env.add_job()
    with pytest.raises(asyncio.CancelledError):
        await env.run(ref, job_try=1)
    assert env.job().status is JobStatus.IN_PROGRESS
    assert file_key(JOB_ID) in env.redis.data

    await env.run(ref, job_try=2)  # arq ponawia zadanie przerwane przez zamknięcie workera
    assert env.job().status is JobStatus.SUCCEEDED
    assert env.job().attempts == 2
    assert env.redis.data == {}


async def test_database_unavailable_is_retried() -> None:
    env = Env()
    ref = await env.add_job()
    env.store.available = False
    with pytest.raises(Retry):
        await env.run(ref, job_try=1)
    with pytest.raises(ConnectionRefusedError):
        await env.run(ref, job_try=3)  # ostatnia próba — błąd zostaje zgłoszony
    assert file_key(JOB_ID) in env.redis.data  # plik wygaśnie po TTL


@pytest.mark.parametrize("finished", [True, False])
async def test_finished_or_deleted_job_is_skipped(finished: bool) -> None:
    env = Env()
    ref = await env.add_job()
    if finished:
        await env.store.mark_started(JOB_ID)
        await env.store.mark_succeeded(JOB_ID, sample_result())
    else:
        await env.store.delete_job(JOB_ID)
    raw = await env.run(ref)
    assert raw == {"status": "skipped"}
    assert env.redis.data == {}
    if finished:
        assert env.job().attempts == 1  # nie uruchomiono ponownie


# ------------------------------------------------------------------ uzgadnianie i retencja


@pytest.mark.parametrize(
    ("status", "state", "age", "expected"),
    [
        (JobStatus.QUEUED, QueueState.NOT_FOUND, 600, JobErrorCode.JOB_LOST),
        (JobStatus.IN_PROGRESS, QueueState.COMPLETE, 600, JobErrorCode.JOB_FAILED),
        (JobStatus.IN_PROGRESS, QueueState.PENDING, 600, None),  # nadal w kolejce/trakcie
        (
            JobStatus.QUEUED,
            QueueState.NOT_FOUND,
            5,
            None,
        ),  # świeże — może jeszcze nie być w kolejce
        (JobStatus.SUCCEEDED, QueueState.NOT_FOUND, 600, None),
    ],
)
async def test_stale_error_rules(
    status: JobStatus, state: QueueState, age: int, expected: JobErrorCode | None
) -> None:
    env = Env()
    await env.add_job()
    now = datetime.now(UTC)
    job = env.job().model_copy(
        update={"status": status, "updated_at": now - timedelta(seconds=age)}
    )
    error = stale_error(job, state, now, timedelta(seconds=120))
    assert (error.code if error else None) is expected


async def test_purge_old_jobs() -> None:
    env = Env()
    await env.add_job()
    env.store.jobs[JOB_ID] = env.job().model_copy(
        update={"created_at": datetime.now(UTC) - timedelta(days=31)}
    )
    assert await purge_old_jobs({"store": env.store, "settings": env.settings}) == 1
    assert env.store.jobs == {}
