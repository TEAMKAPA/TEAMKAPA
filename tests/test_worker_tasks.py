"""Zadanie analyze_document (Valkey zastąpiony słownikiem w pamięci)."""

import hashlib
from pathlib import Path
from typing import Any

import pytest

from backend.shared.config import Settings
from backend.shared.jobs import (
    DocumentRef,
    JobErrorCode,
    JobOutcome,
    deserialize,
    file_key,
    progress_key,
    serialize,
)
from backend.shared.schemas.quality import AnalysisResult
from backend.worker import pipeline
from backend.worker.document import IncomingDocument
from backend.worker.progress import ProgressReporter
from backend.worker.tasks import analyze_document
from tests.api_helpers import sample_result
from tests.file_samples import pdf

JOB_ID = "a" * 32


class FakeRedis:
    def __init__(self) -> None:
        self.data: dict[str, Any] = {}
        self.history: list[tuple[str, str]] = []

    async def get(self, name: str) -> Any:
        return self.data.get(name)

    async def set(self, name: str, value: Any, ex: int | None = None) -> bool:
        self.history.append(("set", name))
        self.data[name] = value
        return True

    async def delete(self, *names: str) -> int:
        for name in names:
            self.history.append(("delete", name))
            self.data.pop(name, None)
        return len(names)


def _document(content: bytes) -> dict[str, Any]:
    ref = DocumentRef(
        job_id=JOB_ID,
        filename="wniosek.pdf",
        extension="pdf",
        mime_type="application/pdf",
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        owner="urzednik",
    )
    return ref.model_dump(mode="json")


async def _run(redis: FakeRedis, document: dict[str, Any]) -> JobOutcome:
    raw = await analyze_document({"redis": redis, "settings": Settings()}, document)
    # Wynik musi przejść przez serializację kolejki (msgpack) bez strat.
    return JobOutcome.model_validate(deserialize(serialize(raw)))


@pytest.fixture
def redis_with_file() -> FakeRedis:
    redis = FakeRedis()
    redis.data[file_key(JOB_ID)] = pdf()
    return redis


@pytest.mark.anyio
async def test_pipeline_not_implemented_yet(redis_with_file: FakeRedis) -> None:
    outcome = await _run(redis_with_file, _document(pdf()))
    assert outcome.status == "failed"
    assert outcome.error is not None
    assert outcome.error.code is JobErrorCode.PIPELINE_NOT_IMPLEMENTED
    assert file_key(JOB_ID) not in redis_with_file.data


@pytest.mark.anyio
async def test_success_cleans_up_file_progress_and_temp_dir(
    redis_with_file: FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, Path] = {}

    async def fake_pipeline(
        document: IncomingDocument, progress: ProgressReporter, settings: Settings
    ) -> AnalysisResult:
        seen["path"] = document.path
        assert document.path.read_bytes() == pdf()
        assert document.path.name == "document.pdf"  # nazwa nie pochodzi od użytkownika
        await progress.update("text_extraction", 2, 10, "Odczyt tekstu")
        assert progress_key(JOB_ID) in redis_with_file.data
        return sample_result(document.ref.sha256)

    monkeypatch.setattr(pipeline, "run_pipeline", fake_pipeline)
    outcome = await _run(redis_with_file, _document(pdf()))

    assert outcome.status == "succeeded"
    assert outcome.result is not None
    assert outcome.result.report.document_sha256 == hashlib.sha256(pdf()).hexdigest()
    assert redis_with_file.data == {}  # plik i postęp usunięte
    assert not seen["path"].exists()
    assert not seen["path"].parent.exists()


@pytest.mark.anyio
async def test_missing_file_reports_expired() -> None:
    outcome = await _run(FakeRedis(), _document(pdf()))
    assert outcome.error is not None
    assert outcome.error.code is JobErrorCode.FILE_EXPIRED


@pytest.mark.anyio
async def test_checksum_mismatch(redis_with_file: FakeRedis) -> None:
    outcome = await _run(redis_with_file, _document(b"inna tresc"))
    assert outcome.error is not None
    assert outcome.error.code is JobErrorCode.FILE_CORRUPTED
    assert file_key(JOB_ID) not in redis_with_file.data


@pytest.mark.anyio
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
async def test_pipeline_errors_are_reported(
    redis_with_file: FakeRedis,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    code: JobErrorCode,
) -> None:
    async def failing(*_: object) -> AnalysisResult:
        raise error

    monkeypatch.setattr(pipeline, "run_pipeline", failing)
    outcome = await _run(redis_with_file, _document(pdf()))
    assert outcome.error is not None
    assert outcome.error.code is code
    assert "szczegóły wewnętrzne" not in outcome.error.message  # brak wycieku szczegółów
    assert redis_with_file.data == {}


@pytest.mark.anyio
async def test_invalid_job_arguments() -> None:
    outcome = await _run(FakeRedis(), {"job_id": "zly"})
    assert outcome.error is not None
    assert outcome.error.code is JobErrorCode.PIPELINE_ERROR
