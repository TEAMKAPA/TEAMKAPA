"""Test end-to-end #16: plik przez API → kolejka → worker → pipeline (etapy 05–09) → baza → API.

Prawdziwe: API, walidacja pliku, Valkey, worker arq, orkiestrator, etapy 05–09 z #6/#8,
normalizacja, reguły COI, ocena pewności, PostgreSQL. Podstawione: ekstrakcja tekstu (#7,
jeszcze niescalona) — reprezentatywny wniosek z pozycjami elementów — oraz odpowiedzi LLM
(deterministyczne odpowiedzi #8, bo testy nie mają GPU).
"""

import asyncio
from typing import Any

import httpx2 as httpx
import jsonschema
import pytest

from backend.api.job_queue import ArqJobQueue
from backend.api.main import create_app
from backend.api.routes import get_mime_detector
from backend.shared.config import ApiSettings, Settings
from backend.shared.db.repository import PgJobStore
from backend.worker.pipeline import orchestrator
from backend.worker.pipeline.stages import PipelineStages
from tests.api_helpers import auth
from tests.coi_helpers import official_schema
from tests.file_samples import pdf, sniff_mime
from tests.integration.conftest import WorkerFactory, needs_postgres, needs_valkey
from tests.pipeline_helpers import stages

pytestmark = [pytest.mark.anyio, needs_postgres, needs_valkey]


async def test_representative_form_through_whole_system(
    settings: Settings, store: PgJobStore, make_worker: WorkerFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    def test_stages(_: Settings) -> PipelineStages:
        return stages()

    monkeypatch.setattr(orchestrator, "default_stages", test_stages)

    async def store_factory(_: ApiSettings) -> PgJobStore:
        return store

    async def queue_factory(api_settings: ApiSettings) -> ArqJobQueue:
        return await ArqJobQueue.connect(api_settings)

    async def keep_pool() -> None:
        return None

    monkeypatch.setattr(store, "close", keep_pool)  # pula należy do fixture'u
    app = create_app(queue_factory=queue_factory, store_factory=store_factory)
    app.dependency_overrides[get_mime_detector] = lambda: sniff_mime
    worker = make_worker(burst=False)
    worker_task = asyncio.create_task(worker.main())
    try:
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://api") as client:
                response = await client.post(
                    "/api/v1/forms/analyze?wait=30",
                    files={"file": ("wniosek_dowod.pdf", pdf(), "application/pdf")},
                    headers=auth(),
                )
                assert response.status_code == 200, response.text
                body: dict[str, Any] = response.json()

                # Plik importu COI zgodny z oficjalnym schematem.
                jsonschema.Draft202012Validator(official_schema()).validate(body["form"])
                assert body["form"]["title"]

                # Raport: każde pole ma poziom pewności, sygnały i źródło w dokumencie.
                report = body["report"]
                assert report["fields"]
                for assessment in report["fields"]:
                    assert assessment["level"] in {"HIGH", "MEDIUM", "LOW"}
                    assert set(assessment["signals"]) >= {"rules", "source_found"}
                with_source = [a for a in report["fields"] if a["sources"]]
                assert with_source
                assert {"page", "bbox", "text", "element_id"} <= set(with_source[0]["sources"][0])
                assert {s["name"] for s in report["stages"]} >= {"text_extraction", "control"}
                assert report["document_type"] == "wniosek_dowod_osobisty"

                # Stan w bazie i w API; wynik można zatwierdzić (brak błędów reguł COI).
                jobs = (await client.get("/api/v1/jobs", headers=auth())).json()
                job_id = jobs["items"][0]["job_id"]
                job = (await client.get(f"/api/v1/jobs/{job_id}", headers=auth())).json()
                assert job["status"] == "SUCCEEDED"
                assert job["result"]["form"] == body["form"]
                approval = await client.put(
                    f"/api/v1/jobs/{job_id}/approval", json=body["form"], headers=auth()
                )
                assert approval.status_code == 200, approval.text
    finally:
        await worker.close()
        worker_task.cancel()
