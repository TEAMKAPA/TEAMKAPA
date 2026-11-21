"""Endpointy REST API (kolejka i baza zastąpione implementacjami w pamięci)."""

import json
import time
from datetime import UTC, datetime, timedelta

import jsonschema
import pytest

from backend.api.main import API_VERSION
from backend.shared.jobs import JobErrorCode, JobStatus
from tests.api_helpers import FakeJobQueue, FakeJobStore, api_client, auth
from tests.coi_helpers import official_schema, sample
from tests.file_samples import pdf, png

ANALYZE = "/api/v1/forms/analyze"
BATCH = "/api/v1/forms/batch"


def _queue(behaviour: str = "succeed", **kwargs: object) -> FakeJobQueue:
    return FakeJobQueue(FakeJobStore(), behaviour, **kwargs)  # type: ignore[arg-type]


def _pdf_upload(name: str = "wniosek.pdf") -> dict[str, tuple[str, bytes, str]]:
    return {"file": (name, pdf(), "application/pdf")}


def _submit(client: object, queue_wait: int = 0, subject: str = "urzednik") -> str:
    response = client.post(  # type: ignore[attr-defined]
        f"{ANALYZE}?wait={queue_wait}", files=_pdf_upload(), headers=auth(subject)
    )
    job_id: str = response.json()["job_id"]
    return job_id


# ------------------------------------------------------------------ system


def test_health_ok() -> None:
    with api_client(_queue()) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": API_VERSION}


def test_ready_checks_queue_and_database() -> None:
    queue = _queue()
    with api_client(queue) as client:
        assert client.get("/ready").status_code == 200
        queue.store.available = False
        response = client.get("/ready")
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "DATABASE_UNAVAILABLE"
    with api_client(_queue("unavailable")) as client:
        assert client.get("/ready").json()["error"]["code"] == "QUEUE_UNAVAILABLE"


def test_openapi_documents_endpoints() -> None:
    with api_client(_queue()) as client:
        spec = client.get("/api/openapi.json").json()
    assert spec["info"]["title"] == "COI Cyfryzacja formularzy"
    assert {
        ANALYZE,
        BATCH,
        "/api/v1/jobs",
        "/api/v1/jobs/{job_id}",
        "/api/v1/jobs/{job_id}/approval",
        "/api/v1/batches/{batch_id}",
    } <= set(spec["paths"])
    assert "202" in spec["paths"][ANALYZE]["post"]["responses"]


def test_resources_closed_on_shutdown() -> None:
    queue = _queue()
    with api_client(queue):
        pass
    assert queue.closed
    assert queue.store.closed


# ------------------------------------------------------------------ uwierzytelnianie


def test_missing_token_rejected() -> None:
    with api_client(_queue()) as client:
        response = client.post(ANALYZE, files=_pdf_upload())
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json()["error"]["code"] == "AUTH_REQUIRED"


def test_invalid_token_rejected() -> None:
    with api_client(_queue()) as client:
        response = client.post(
            ANALYZE, files=_pdf_upload(), headers={"Authorization": "Bearer abc.def.ghi"}
        )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "TOKEN_INVALID"


def test_jwt_disabled_allows_anonymous(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_ENABLED", "false")
    with api_client(_queue()) as client:
        assert client.post(ANALYZE, files=_pdf_upload()).status_code == 200


# ------------------------------------------------------------------ analyze


def test_analyze_returns_coi_json_and_report() -> None:
    queue = _queue("succeed")
    with api_client(queue) as client:
        response = client.post(ANALYZE, files=_pdf_upload(), headers=auth())
    assert response.status_code == 200
    body = response.json()
    assert body["form"] == sample()
    jsonschema.Draft202012Validator(official_schema()).validate(body["form"])
    assert "null" not in json.dumps(body["form"])
    assert list(queue.files.values()) == [pdf()]  # treść pliku trafiła tylko do kolejki
    (job,) = queue.store.jobs.values()
    assert job.status is JobStatus.SUCCEEDED
    assert job.filename == "wniosek.pdf"


def test_analyze_returns_202_when_not_finished(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANALYZE_WAIT_SECONDS", "1")
    with api_client(_queue("pending")) as client:
        response = client.post(ANALYZE, files=_pdf_upload(), headers=auth())
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "QUEUED"
    assert body["filename"] == "wniosek.pdf"
    assert response.headers["location"] == body["status_url"] == f"/api/v1/jobs/{body['job_id']}"
    assert response.headers["retry-after"] == "5"


def test_analyze_wait_zero_returns_immediately() -> None:
    with api_client(_queue("succeed")) as client:
        response = client.post(f"{ANALYZE}?wait=0", files=_pdf_upload(), headers=auth())
    assert response.status_code == 202


def test_analyze_wait_capped_by_max(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANALYZE_MAX_WAIT_SECONDS", "1")
    monkeypatch.setenv("ANALYZE_WAIT_SECONDS", "1")
    with api_client(_queue("pending")) as client:
        started = time.monotonic()
        response = client.post(f"{ANALYZE}?wait=9999", files=_pdf_upload(), headers=auth())
        elapsed = time.monotonic() - started
    assert response.status_code == 202
    assert 0.9 <= elapsed < 3


@pytest.mark.parametrize(
    ("code", "status"),
    [
        (JobErrorCode.PIPELINE_NOT_IMPLEMENTED, 501),
        (JobErrorCode.DOCUMENT_UNREADABLE, 422),
        (JobErrorCode.PIPELINE_UNAVAILABLE, 503),
        (JobErrorCode.PIPELINE_TIMEOUT, 504),
        (JobErrorCode.PIPELINE_ERROR, 500),
    ],
)
def test_analyze_failed_job(code: JobErrorCode, status: int) -> None:
    with api_client(_queue("fail", error_code=code)) as client:
        response = client.post(ANALYZE, files=_pdf_upload(), headers=auth())
    assert response.status_code == status
    error = response.json()["error"]
    assert error["code"] == code
    assert len(error["details"]["job_id"]) == 32


def test_queue_unavailable_leaves_no_job() -> None:
    queue = _queue("unavailable")
    with api_client(queue) as client:
        response = client.post(ANALYZE, files=_pdf_upload(), headers=auth())
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "QUEUE_UNAVAILABLE"
    assert response.headers["retry-after"] == "5"
    assert queue.store.jobs == {}


def test_database_unavailable_returns_503() -> None:
    queue = _queue()
    queue.store.available = False
    with api_client(queue) as client:
        response = client.post(ANALYZE, files=_pdf_upload(), headers=auth())
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DATABASE_UNAVAILABLE"
    assert queue.files == {}


@pytest.mark.parametrize(
    ("name", "content", "status", "code"),
    [
        ("wniosek;1.pdf", pdf(), 400, "FILENAME_FORBIDDEN_CHARS"),
        ("a" * 247 + ".pdf", pdf(), 400, "FILENAME_TOO_LONG"),
        ("wniosek.exe", pdf(), 415, "EXTENSION_NOT_ALLOWED"),
        ("wniosek.pdf", png(), 415, "MIME_MISMATCH"),
        ("wniosek.pdf", b"", 400, "FILE_EMPTY"),
    ],
)
def test_analyze_rejects_invalid_files(name: str, content: bytes, status: int, code: str) -> None:
    queue = _queue()
    with api_client(queue) as client:
        response = client.post(
            ANALYZE, files={"file": (name, content, "application/pdf")}, headers=auth()
        )
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert queue.files == {}
    assert queue.store.jobs == {}


def test_analyze_rejects_too_large_file(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_FILE_SIZE_MB", "1")
    big = pdf() + b"0" * (1024 * 1024)
    with api_client(_queue()) as client:
        response = client.post(
            ANALYZE, files={"file": ("duzy.pdf", big, "application/pdf")}, headers=auth()
        )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "FILE_TOO_LARGE"


def test_request_body_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_FILE_SIZE_MB", "1")
    monkeypatch.setenv("MAX_BATCH_FILES", "1")
    huge = b"0" * (3 * 1024 * 1024)
    with api_client(_queue()) as client:
        response = client.post(
            ANALYZE, files={"file": ("duzy.pdf", huge, "application/pdf")}, headers=auth()
        )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "REQUEST_TOO_LARGE"


def test_analyze_requires_file() -> None:
    with api_client(_queue()) as client:
        response = client.post(ANALYZE, headers=auth())
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "REQUEST_INVALID"


# ------------------------------------------------------------------ batch


def _mixed_batch() -> list[tuple[str, tuple[str, bytes, str]]]:
    return [
        ("files", ("a.pdf", pdf(), "application/pdf")),
        ("files", ("b.png", png(), "image/png")),
        ("files", ("c.exe", b"MZ", "application/octet-stream")),
        ("files", ("d.png", pdf(), "image/png")),
    ]


def test_batch_accepts_valid_and_reports_rejected() -> None:
    queue = _queue("pending")
    with api_client(queue) as client:
        response = client.post(BATCH, files=_mixed_batch(), headers=auth())
        assert response.status_code == 202
        body = response.json()
        assert [j["filename"] for j in body["jobs"]] == ["a.pdf", "b.png"]
        assert [(r["filename"], r["code"]) for r in body["rejected"]] == [
            ("c.exe", "EXTENSION_NOT_ALLOWED"),
            ("d.png", "MIME_MISMATCH"),
        ]
        assert body["status_url"] == f"/api/v1/batches/{body['batch_id']}"

        batch = client.get(body["status_url"], headers=auth()).json()
    assert batch["total_files"] == 4
    assert batch["finished"] is False
    assert batch["counts"] == {"QUEUED": 2, "IN_PROGRESS": 0, "SUCCEEDED": 0, "FAILED": 0}
    assert {j["filename"] for j in batch["jobs"]} == {"a.pdf", "b.png"}
    assert len(batch["rejected"]) == 2


def test_batch_finished_when_all_done() -> None:
    with api_client(_queue("succeed")) as client:
        body = client.post(BATCH, files=_mixed_batch()[:2], headers=auth()).json()
        batch = client.get(body["status_url"], headers=auth()).json()
        other = client.get(body["status_url"], headers=auth("ktos-inny"))
    assert batch["finished"] is True
    assert batch["counts"]["SUCCEEDED"] == 2
    assert other.status_code == 404
    assert other.json()["error"]["code"] == "BATCH_NOT_FOUND"


def test_batch_queue_failure_midway_is_reported() -> None:
    queue = _queue("pending")
    queue.fail_after = 1
    with api_client(queue) as client:
        body = client.post(BATCH, files=_mixed_batch()[:2], headers=auth()).json()
        batch = client.get(body["status_url"], headers=auth()).json()
    assert [j["filename"] for j in body["jobs"]] == ["a.pdf"]
    assert [(r["filename"], r["code"]) for r in body["rejected"]] == [
        ("b.png", "QUEUE_UNAVAILABLE")
    ]
    assert batch["rejected"] == body["rejected"]
    assert len(queue.store.jobs) == 1


def test_batch_queue_down_creates_nothing() -> None:
    queue = _queue("unavailable")
    with api_client(queue) as client:
        response = client.post(BATCH, files=_mixed_batch()[:2], headers=auth())
    assert response.status_code == 503
    assert queue.store.batches == {}
    assert queue.store.jobs == {}


def test_batch_all_rejected() -> None:
    with api_client(_queue()) as client:
        response = client.post(
            BATCH, files=[("files", ("x.exe", b"MZ", "application/octet-stream"))], headers=auth()
        )
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "BATCH_ALL_REJECTED"
    assert error["details"][0]["code"] == "EXTENSION_NOT_ALLOWED"


def test_batch_too_many_files(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_BATCH_FILES", "2")
    files = [("files", (f"{i}.pdf", pdf(), "application/pdf")) for i in range(3)]
    queue = _queue()
    with api_client(queue) as client:
        response = client.post(BATCH, files=files, headers=auth())
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "TOO_MANY_FILES"
    assert queue.files == {}


# ------------------------------------------------------------------ zadania


def test_job_in_progress_shows_progress() -> None:
    with api_client(_queue("in_progress")) as client:
        body = client.get(f"/api/v1/jobs/{_submit(client)}", headers=auth()).json()
    assert body["status"] == "IN_PROGRESS"
    assert body["attempts"] == 1
    assert body["progress"] == {"stage": "text_extraction", "step": 2, "total_steps": 10}
    assert "result" not in body


def test_job_succeeded_contains_result() -> None:
    with api_client(_queue("succeed")) as client:
        body = client.get(f"/api/v1/jobs/{_submit(client)}", headers=auth()).json()
    assert body["status"] == "SUCCEEDED"
    assert body["result"]["form"] == sample()
    assert "progress" not in body


def test_job_retry_reason_visible_while_queued() -> None:
    queue = _queue("pending")
    with api_client(queue) as client:
        job_id = _submit(client)
        queue.store.jobs[job_id] = queue.store.jobs[job_id].model_copy(
            update={"attempts": 1, "last_error_message": "Ponowienie za 30 s: LLM niedostępny"}
        )
        body = client.get(f"/api/v1/jobs/{job_id}", headers=auth()).json()
    assert body["status"] == "QUEUED"
    assert body["retry_reason"] == "Ponowienie za 30 s: LLM niedostępny"


def test_job_of_other_user_is_not_found() -> None:
    with api_client(_queue()) as client:
        job_id = _submit(client, subject="ktos-inny")
        response = client.get(f"/api/v1/jobs/{job_id}", headers=auth("urzednik"))
        assert client.get(f"/api/v1/jobs/{job_id}", headers=auth("ktos-inny")).status_code == 200
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "JOB_NOT_FOUND"


def test_unknown_and_malformed_job_ids() -> None:
    with api_client(_queue()) as client:
        assert client.get(f"/api/v1/jobs/{'a' * 32}", headers=auth()).status_code == 404
        assert client.get("/api/v1/jobs/XYZ", headers=auth()).status_code == 422


@pytest.mark.parametrize(
    ("queue_state", "expected_code"), [("NOT_FOUND", "JOB_LOST"), ("COMPLETE", "JOB_FAILED")]
)
def test_stale_job_reconciled_on_read(
    monkeypatch: pytest.MonkeyPatch, queue_state: str, expected_code: str
) -> None:
    from backend.shared.jobs import QueueState

    monkeypatch.setenv("STALE_JOB_GRACE_SECONDS", "10")
    queue = _queue("pending")
    with api_client(queue) as client:
        job_id = _submit(client)
        assert client.get(f"/api/v1/jobs/{job_id}", headers=auth()).json()["status"] == "QUEUED"
        queue.states[job_id] = QueueState(queue_state)
        # świeże zadanie nie jest uzgadniane (mogło jeszcze nie trafić do kolejki)
        assert client.get(f"/api/v1/jobs/{job_id}", headers=auth()).json()["status"] == "QUEUED"
        old = datetime.now(UTC) - timedelta(minutes=5)
        queue.store.jobs[job_id] = queue.store.jobs[job_id].model_copy(update={"updated_at": old})
        body = client.get(f"/api/v1/jobs/{job_id}", headers=auth()).json()
    assert body["status"] == "FAILED"
    assert body["error"]["code"] == expected_code


def test_list_jobs_with_filters_and_pagination() -> None:
    queue = _queue("succeed")
    with api_client(queue) as client:
        ids = [_submit(client) for _ in range(3)]
        queue.behaviour = "pending"
        pending_id = _submit(client)
        _submit(client, subject="ktos-inny")

        page = client.get("/api/v1/jobs?limit=2", headers=auth()).json()
        rest = client.get("/api/v1/jobs?limit=2&offset=2", headers=auth()).json()
        queued = client.get("/api/v1/jobs?status=QUEUED", headers=auth()).json()
        invalid = client.get("/api/v1/jobs?status=DONE", headers=auth())
    assert page["total"] == 4
    assert len(page["items"]) == 2
    assert {i["job_id"] for i in page["items"] + rest["items"]} == {*ids, pending_id}
    assert [i["job_id"] for i in queued["items"]] == [pending_id]
    assert "result" not in page["items"][0]
    assert invalid.status_code == 422


# ------------------------------------------------------------------ zatwierdzanie


def test_approve_and_read_back() -> None:
    with api_client(_queue("succeed")) as client:
        job_id = _submit(client)
        form = sample()
        form["title"] = "Wniosek o ustalenie numeru porządkowego — poprawiony"
        response = client.put(f"/api/v1/jobs/{job_id}/approval", json=form, headers=auth())
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["approved_by"] == "urzednik"
        assert body["form"] == form
        assert body["warnings"] == []

        again = client.get(f"/api/v1/jobs/{job_id}/approval", headers=auth()).json()
        job = client.get(f"/api/v1/jobs/{job_id}", headers=auth()).json()
        listed = client.get("/api/v1/jobs", headers=auth()).json()["items"][0]
    assert again["form"]["title"] == form["title"]
    assert job["approved_at"] == body["approved_at"]
    assert listed["approved_at"] == body["approved_at"]


def test_approve_returns_warnings() -> None:
    with api_client(_queue("succeed")) as client:
        job_id = _submit(client)
        form = sample()
        del form["tabs"][1]["questions"][0]["field"]["repetingSectionGrid"][1]["field"]["title"]
        body = client.put(f"/api/v1/jobs/{job_id}/approval", json=form, headers=auth()).json()
    assert [w["code"] for w in body["warnings"]] == ["INPUT_WITHOUT_TITLE"]


def test_approve_rejects_rule_errors() -> None:
    with api_client(_queue("succeed")) as client:
        job_id = _submit(client)
        form = sample()
        form["tabs"][-1]["type"] = "MAIN"
        response = client.put(f"/api/v1/jobs/{job_id}/approval", json=form, headers=auth())
        missing = client.get(f"/api/v1/jobs/{job_id}/approval", headers=auth())
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "FORM_RULES_VIOLATED"
    assert "FINISH_TAB_LAST" in {d["code"] for d in error["details"]}
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "APPROVAL_NOT_FOUND"


def test_approve_rejects_invalid_structure() -> None:
    with api_client(_queue("succeed")) as client:
        job_id = _submit(client)
        response = client.put(
            f"/api/v1/jobs/{job_id}/approval", json={"title": "x"}, headers=auth()
        )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "REQUEST_INVALID"


def test_approve_requires_succeeded_job() -> None:
    with api_client(_queue("fail")) as client:
        job_id = _submit(client)
        response = client.put(f"/api/v1/jobs/{job_id}/approval", json=sample(), headers=auth())
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "JOB_NOT_SUCCEEDED"


def test_approve_other_users_job_not_found() -> None:
    with api_client(_queue("succeed")) as client:
        job_id = _submit(client, subject="ktos-inny")
        response = client.put(f"/api/v1/jobs/{job_id}/approval", json=sample(), headers=auth())
    assert response.status_code == 404
