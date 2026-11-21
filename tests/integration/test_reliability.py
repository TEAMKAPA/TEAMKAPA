"""Niezawodność kolejki: prawdziwy worker arq, Valkey i PostgreSQL.

Scenariusze awarii workera i ponowień (kryterium akceptacji #15):
- błąd przejściowy → ponowienia → sukces; wyczerpanie limitu prób → FAILED,
- zamknięcie workera w trakcie analizy → inny worker kończy zadanie,
- twarda awaria (proces zabity) → wznowienie zadania z IN_PROGRESS,
- przekroczony limit prób po awariach → uzgodnienie jako FAILED,
- utrata kolejki (restart Valkey) → uzgodnienie jako FAILED (JOB_LOST),
- treść pliku nie trafia do bazy ani nie zostaje w Valkey.
"""

import asyncio
import hashlib
from typing import Any
from uuid import uuid4

import httpx2 as httpx
import pytest
from arq.connections import ArqRedis

from backend.api.job_queue import ArqJobQueue
from backend.api.main import create_app
from backend.api.routes import get_mime_detector
from backend.shared.config import ApiSettings, Settings
from backend.shared.db.repository import PgJobStore
from backend.shared.jobs import ANALYZE_TASK, DocumentRef, JobErrorCode, JobStatus, file_key
from backend.shared.schemas.quality import AnalysisResult
from backend.worker import pipeline
from backend.worker.document import IncomingDocument
from backend.worker.progress import ProgressReporter
from backend.worker.tasks import reconcile_stale_jobs
from tests.api_helpers import auth
from tests.coi_helpers import sample
from tests.fakes import sample_result
from tests.file_samples import pdf, sniff_mime
from tests.integration.conftest import WorkerFactory, needs_postgres, needs_valkey

pytestmark = [pytest.mark.anyio, needs_postgres, needs_valkey]

MARKER = b"UNIKALNY-ZNACZNIK-TRESCI-PLIKU-1234567890"


def _document(content: bytes) -> DocumentRef:
    return DocumentRef(
        job_id=uuid4().hex,
        filename="wniosek.pdf",
        extension="pdf",
        mime_type="application/pdf",
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        owner="urzednik",
    )


async def _submit(store: PgJobStore, queue: ArqJobQueue, content: bytes = pdf()) -> DocumentRef:
    document = _document(content)
    await store.create_job(document)
    await queue.submit(document, content)
    return document


@pytest.fixture
async def queue(settings: Settings, redis: ArqRedis) -> Any:
    q = ArqJobQueue(redis, settings)
    yield q


def _pipeline(monkeypatch: pytest.MonkeyPatch, behaviour: Any) -> dict[str, int]:
    calls = {"n": 0}

    async def run(
        document: IncomingDocument, progress: ProgressReporter, settings: Settings
    ) -> AnalysisResult:
        calls["n"] += 1
        await progress.update("text_extraction", 1, 10)
        result: AnalysisResult = await behaviour(calls["n"], document)
        return result

    monkeypatch.setattr(pipeline, "run_pipeline", run)
    return calls


async def _succeed(_: int, document: IncomingDocument) -> AnalysisResult:
    return sample_result(document.ref.sha256)


async def _job(store: PgJobStore, document: DocumentRef) -> Any:
    job = await store.get_job(document.job_id)
    assert job is not None
    return job


async def _wait_status(store: PgJobStore, document: DocumentRef, status: JobStatus) -> None:
    for _ in range(200):
        if (await _job(store, document)).status is status:
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"zadanie nie osiągnęło statusu {status}")


# ------------------------------------------------------------------ szczęśliwa ścieżka


async def test_success_persists_result_and_removes_file(
    store: PgJobStore, queue: ArqJobQueue, redis: ArqRedis, db_pool: Any,
    make_worker: WorkerFactory, monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    _pipeline(monkeypatch, _succeed)
    document = await _submit(store, queue, pdf() + MARKER)
    await make_worker().main()

    job = await _job(store, document)
    assert (job.status, job.attempts) == (JobStatus.SUCCEEDED, 1)
    assert job.result is not None
    assert job.result.form.to_coi_json() == sample()
    assert await redis.exists(file_key(document.job_id)) == 0

    # Treść pliku nie trafiła do bazy ani nie została w Valkey.
    for table in ("jobs", "batches", "approved_forms", "schema_migrations"):
        rows = await db_pool.fetch(f"SELECT row_to_json(t)::text AS r FROM {table} t")  # noqa: S608
        assert not any(MARKER.decode() in row["r"] for row in rows), table
    async for key in redis.scan_iter():
        if await redis.type(key) == b"string":
            assert MARKER not in (await redis.get(key) or b""), key


# ------------------------------------------------------------------ ponowienia


async def test_transient_errors_retried_until_success(
    store: PgJobStore, queue: ArqJobQueue, make_worker: WorkerFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    async def flaky(call: int, document: IncomingDocument) -> AnalysisResult:
        if call < 3:
            raise pipeline.PipelineTemporarilyUnavailableError("serwer LLM niedostępny")
        return sample_result(document.ref.sha256)

    calls = _pipeline(monkeypatch, flaky)
    document = await _submit(store, queue)
    await make_worker().main()

    job = await _job(store, document)
    assert calls["n"] == 3
    assert (job.status, job.attempts) == (JobStatus.SUCCEEDED, 3)


async def test_retry_limit_exhausted(
    store: PgJobStore, queue: ArqJobQueue, redis: ArqRedis, make_worker: WorkerFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    async def down(*_: object) -> AnalysisResult:
        raise pipeline.PipelineTemporarilyUnavailableError("serwer LLM niedostępny")

    calls = _pipeline(monkeypatch, down)
    document = await _submit(store, queue)
    await make_worker().main()

    job = await _job(store, document)
    assert calls["n"] == 3  # MAX_JOB_TRIES
    assert (job.status, job.attempts) == (JobStatus.FAILED, 3)
    assert job.error.code is JobErrorCode.PIPELINE_UNAVAILABLE
    assert await redis.exists(file_key(document.job_id)) == 0


# ------------------------------------------------------------------ awarie workera


async def test_worker_shutdown_mid_job_is_resumed_by_another_worker(
    store: PgJobStore, queue: ArqJobQueue, make_worker: WorkerFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    async def hangs_first(call: int, document: IncomingDocument) -> AnalysisResult:
        if call == 1:
            await asyncio.sleep(3600)  # przerwane przez zamknięcie workera
        return sample_result(document.ref.sha256)

    calls = _pipeline(monkeypatch, hangs_first)
    document = await _submit(store, queue)

    first = make_worker(burst=False)
    first_task = asyncio.create_task(first.main())
    await _wait_status(store, document, JobStatus.IN_PROGRESS)
    await first.close()  # łagodne zamknięcie (jak SIGTERM) w trakcie analizy
    first_task.cancel()

    await make_worker().main()  # drugi worker przejmuje zadanie
    job = await _job(store, document)
    assert calls["n"] == 2
    assert (job.status, job.attempts) == (JobStatus.SUCCEEDED, 2)


async def test_hard_crash_job_resumed_from_in_progress(
    store: PgJobStore, redis: ArqRedis, make_worker: WorkerFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    _pipeline(monkeypatch, _succeed)
    content = pdf()
    document = _document(content)
    await store.create_job(document)
    # Stan po zabitym procesie: zadanie podjęte raz (IN_PROGRESS w bazie, licznik prób arq = 1),
    # blokada „w trakcie” wygasła, zadanie i plik wciąż w kolejce.
    await store.mark_started(document.job_id)
    await redis.set(file_key(document.job_id), content, ex=3600)
    await redis.enqueue_job(ANALYZE_TASK, document.model_dump(mode="json"), _job_id=document.job_id)
    await redis.set(f"arq:retry:{document.job_id}", 1)

    await make_worker().main()
    job = await _job(store, document)
    assert (job.status, job.attempts) == (JobStatus.SUCCEEDED, 2)


async def test_retry_limit_after_crashes_is_reconciled(
    store: PgJobStore, redis: ArqRedis, db_pool: Any, settings: Settings,
    make_worker: WorkerFactory, monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    calls = _pipeline(monkeypatch, _succeed)
    content = pdf()
    document = _document(content)
    await store.create_job(document)
    await store.mark_started(document.job_id)
    await redis.set(file_key(document.job_id), content, ex=3600)
    await redis.enqueue_job(ANALYZE_TASK, document.model_dump(mode="json"), _job_id=document.job_id)
    # Proces zabity przy każdej z MAX_JOB_TRIES prób — arq odrzuca kolejną bez wywołania zadania.
    await redis.set(f"arq:retry:{document.job_id}", settings.max_job_tries)

    await make_worker().main()
    assert calls["n"] == 0
    assert (await _job(store, document)).status is JobStatus.IN_PROGRESS

    await db_pool.execute(
        "UPDATE jobs SET updated_at = now() - interval '1 hour' WHERE id = $1", document.job_id
    )
    ctx = {"store": store, "redis": redis, "settings": settings}
    assert await reconcile_stale_jobs(ctx) == 1
    job = await _job(store, document)
    assert job.status is JobStatus.FAILED
    assert job.error.code is JobErrorCode.JOB_FAILED
    assert await redis.exists(file_key(document.job_id)) == 0


async def test_lost_queue_is_reconciled(
    store: PgJobStore, queue: ArqJobQueue, redis: ArqRedis, db_pool: Any, settings: Settings
) -> None:
    document = await _submit(store, queue)
    await redis.flushdb()  # restart Valkey bez trwałości
    ctx = {"store": store, "redis": redis, "settings": settings}
    assert await reconcile_stale_jobs(ctx) == 0  # świeże zadanie — jeszcze nie ruszamy

    await db_pool.execute(
        "UPDATE jobs SET updated_at = now() - interval '1 hour' WHERE id = $1", document.job_id
    )
    assert await reconcile_stale_jobs(ctx) == 1
    job = await _job(store, document)
    assert job.status is JobStatus.FAILED
    assert job.error.code is JobErrorCode.JOB_LOST


# ------------------------------------------------------------------ API end-to-end


async def test_api_end_to_end(
    settings: Settings, pg_schema: str, store: PgJobStore, make_worker: WorkerFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    _pipeline(monkeypatch, _succeed)

    async def store_factory(_: ApiSettings) -> PgJobStore:
        return store

    async def queue_factory(api_settings: ApiSettings) -> ArqJobQueue:
        return await ArqJobQueue.connect(api_settings)

    app = create_app(queue_factory=queue_factory, store_factory=store_factory)
    app.dependency_overrides[get_mime_detector] = lambda: sniff_mime
    monkeypatch.setattr(store, "close", _noop)  # pula należy do fixture'u
    worker = make_worker(burst=False)
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
                assert response.json()["form"] == sample()

                batch = (
                    await client.post(
                        "/api/v1/forms/batch",
                        files=[
                            ("files", ("a.pdf", pdf(), "application/pdf")),
                            ("files", ("b.exe", b"MZ", "application/octet-stream")),
                        ],
                        headers=auth(),
                    )
                ).json()
                for _ in range(100):
                    view = (await client.get(batch["status_url"], headers=auth())).json()
                    if view["finished"]:
                        break
                    await asyncio.sleep(0.1)
                assert view["counts"]["SUCCEEDED"] == 1
                assert [r["code"] for r in view["rejected"]] == ["EXTENSION_NOT_ALLOWED"]

                job_id = view["jobs"][0]["job_id"]
                approval = await client.put(
                    f"/api/v1/jobs/{job_id}/approval", json=sample(), headers=auth()
                )
                assert approval.status_code == 200, approval.text
                listed = (await client.get("/api/v1/jobs", headers=auth())).json()
                assert listed["total"] == 2
                assert {i["job_id"]: i.get("approved_at") is not None for i in listed["items"]}[
                    job_id
                ]
    finally:
        await worker.close()
        worker_task.cancel()


async def _noop() -> None:
    return None
