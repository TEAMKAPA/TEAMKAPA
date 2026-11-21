"""Integracja z prawdziwym Valkey i workerem arq.

Uruchamiane, gdy ustawiono ``VALKEY_TEST_URL`` (CI: kontener usługi; lokalnie np.
``docker run -p 6379:6379 valkey/valkey:9.0.8-alpine3.24``). Baza testowa jest czyszczona.
"""

import asyncio
import hashlib
import os
from collections.abc import AsyncIterator
from typing import Any

import httpx2 as httpx
import pytest
from arq.connections import RedisSettings
from arq.worker import Worker

from backend.api.job_queue import ArqJobQueue
from backend.api.main import create_app
from backend.api.routes import get_mime_detector
from backend.shared.config import ApiSettings, Settings
from backend.shared.jobs import (
    DocumentRef,
    JobErrorCode,
    JobStatus,
    deserialize,
    file_key,
    serialize,
)
from backend.shared.schemas.quality import AnalysisResult
from backend.worker import pipeline
from backend.worker.document import IncomingDocument
from backend.worker.progress import ProgressReporter
from backend.worker.tasks import analyze_document
from tests.api_helpers import auth, sample_result
from tests.file_samples import pdf, sniff_mime

VALKEY_URL = os.environ.get("VALKEY_TEST_URL")
pytestmark = [
    pytest.mark.anyio,
    pytest.mark.skipif(not VALKEY_URL, reason="ustaw VALKEY_TEST_URL, aby uruchomić integrację"),
]

MARKER = b"UNIKALNY-ZNACZNIK-TRESCI-PLIKU-1234567890"


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    assert VALKEY_URL is not None
    monkeypatch.setenv("VALKEY_URL", VALKEY_URL)
    return Settings()


@pytest.fixture
async def queue(settings: Settings) -> AsyncIterator[ArqJobQueue]:
    q = await ArqJobQueue.connect(settings)
    await q._redis.flushdb()
    yield q
    await q._redis.flushdb()
    await q.close()


def _worker(settings: Settings, *, burst: bool) -> Worker:
    async def startup(ctx: dict[str, Any]) -> None:
        ctx["settings"] = settings

    return Worker(
        functions=[analyze_document],
        redis_settings=RedisSettings.from_dsn(settings.valkey_url),
        job_serializer=serialize,
        job_deserializer=deserialize,
        on_startup=startup,
        burst=burst,
        poll_delay=0.05,
        keep_result=settings.job_result_ttl_seconds,
        handle_signals=False,
    )


def _ref(content: bytes, owner: str = "urzednik") -> DocumentRef:
    return DocumentRef(
        job_id=hashlib.md5(content + owner.encode(), usedforsecurity=False).hexdigest(),
        filename="wniosek.pdf",
        extension="pdf",
        mime_type="application/pdf",
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        owner=owner,
    )


@pytest.fixture
def fake_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run(
        document: IncomingDocument, progress: ProgressReporter, settings: Settings
    ) -> AnalysisResult:
        await progress.update("text_extraction", 1, 10)
        return sample_result(document.ref.sha256)

    monkeypatch.setattr(pipeline, "run_pipeline", run)


async def _all_values(queue: ArqJobQueue) -> list[bytes]:
    redis = queue._redis
    values: list[bytes] = []
    async for key in redis.scan_iter():
        if await redis.type(key) == b"string":
            value = await redis.get(key)
            if value is not None:
                values.append(value)
    return values


async def test_job_lifecycle_and_file_removed(
    queue: ArqJobQueue, settings: Settings, fake_pipeline: None
) -> None:
    content = pdf() + MARKER
    ref = _ref(content)
    await queue.submit(ref, content)

    queued = await queue.get(ref.job_id)
    assert queued is not None
    assert queued.status is JobStatus.QUEUED
    assert queued.document == ref
    assert await queue._redis.ttl(file_key(ref.job_id)) > 0

    await _worker(settings, burst=True).main()

    done = await queue.get(ref.job_id)
    assert done is not None
    assert done.status is JobStatus.SUCCEEDED
    assert done.result is not None
    assert done.result.report.document_sha256 == ref.sha256
    # Plik usunięty z kolejki; jego treść nie została w argumentach ani w wyniku zadania.
    assert await queue._redis.exists(file_key(ref.job_id)) == 0
    assert not any(MARKER in value for value in await _all_values(queue))


async def test_failed_job_visible_as_failed(queue: ArqJobQueue, settings: Settings) -> None:
    content = pdf()
    ref = _ref(content, owner="inny")
    await queue.submit(ref, content)
    await _worker(settings, burst=True).main()

    done = await queue.get(ref.job_id)
    assert done is not None
    assert done.status is JobStatus.FAILED
    assert done.error is not None
    assert done.error.code is JobErrorCode.PIPELINE_NOT_IMPLEMENTED


async def test_unknown_job(queue: ArqJobQueue) -> None:
    assert await queue.get("0" * 32) is None


async def test_wait_times_out_without_worker(queue: ArqJobQueue) -> None:
    content = pdf()
    ref = _ref(content, owner="czeka")
    await queue.submit(ref, content)
    snapshot = await queue.wait(ref.job_id, 0.3)
    assert snapshot is not None
    assert snapshot.status is JobStatus.QUEUED


async def test_api_end_to_end_with_worker(
    queue: ArqJobQueue, settings: Settings, fake_pipeline: None
) -> None:
    api_settings = ApiSettings()

    async def factory(_: ApiSettings) -> ArqJobQueue:
        return await ArqJobQueue.connect(api_settings)

    app = create_app(queue_factory=factory)
    app.dependency_overrides[get_mime_detector] = lambda: sniff_mime
    worker = _worker(settings, burst=False)
    worker_task = asyncio.create_task(worker.main())
    try:
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://api") as client:
                response = await client.post(
                    "/api/v1/forms/analyze?wait=20",
                    files={"file": ("wniosek.pdf", pdf(), "application/pdf")},
                    headers=auth(),
                )
                assert response.status_code == 200, response.text
                assert response.json()["form"]["title"].startswith("Wniosek")

                accepted = await client.post(
                    "/api/v1/forms/analyze?wait=0",
                    files={"file": ("drugi.pdf", pdf(), "application/pdf")},
                    headers=auth(),
                )
                assert accepted.status_code == 202
                status_url = accepted.json()["status_url"]
                for _ in range(100):
                    job = (await client.get(status_url, headers=auth())).json()
                    if job["status"] == "SUCCEEDED":
                        break
                    await asyncio.sleep(0.1)
                assert job["status"] == "SUCCEEDED"
                assert (await client.get(status_url, headers=auth("ktos-inny"))).status_code == 404
    finally:
        await worker.close()
        worker_task.cancel()
