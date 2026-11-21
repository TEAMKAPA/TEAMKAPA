"""Endpointy REST API (kolejka zastąpiona implementacją w pamięci)."""

import json

import jsonschema
import pytest

from backend.api.main import API_VERSION
from backend.shared.jobs import JobErrorCode
from tests.api_helpers import FakeJobQueue, api_client, auth
from tests.coi_helpers import official_schema, sample
from tests.file_samples import pdf, png

ANALYZE = "/api/v1/forms/analyze"
BATCH = "/api/v1/forms/batch"


def _pdf_upload(name: str = "wniosek.pdf") -> dict[str, tuple[str, bytes, str]]:
    return {"file": (name, pdf(), "application/pdf")}


# ------------------------------------------------------------------ system


def test_health_ok() -> None:
    with api_client(FakeJobQueue()) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": API_VERSION}


def test_ready_reflects_queue() -> None:
    with api_client(FakeJobQueue()) as client:
        assert client.get("/ready").status_code == 200
    with api_client(FakeJobQueue("unavailable")) as client:
        response = client.get("/ready")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "QUEUE_UNAVAILABLE"


def test_openapi_documents_endpoints() -> None:
    with api_client(FakeJobQueue()) as client:
        spec = client.get("/api/openapi.json").json()
    assert spec["info"]["title"] == "COI Cyfryzacja formularzy"
    assert {ANALYZE, BATCH, "/api/v1/jobs/{job_id}"} <= set(spec["paths"])
    assert "202" in spec["paths"][ANALYZE]["post"]["responses"]


def test_queue_closed_on_shutdown() -> None:
    queue = FakeJobQueue()
    with api_client(queue):
        pass
    assert queue.closed


# ------------------------------------------------------------------ uwierzytelnianie


def test_missing_token_rejected() -> None:
    with api_client(FakeJobQueue()) as client:
        response = client.post(ANALYZE, files=_pdf_upload())
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json()["error"]["code"] == "AUTH_REQUIRED"


def test_invalid_token_rejected() -> None:
    with api_client(FakeJobQueue()) as client:
        response = client.post(
            ANALYZE, files=_pdf_upload(), headers={"Authorization": "Bearer abc.def.ghi"}
        )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "TOKEN_INVALID"


def test_jwt_disabled_allows_anonymous(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_ENABLED", "false")
    with api_client(FakeJobQueue()) as client:
        assert client.post(ANALYZE, files=_pdf_upload()).status_code == 200


# ------------------------------------------------------------------ analyze


def test_analyze_returns_coi_json_and_report() -> None:
    queue = FakeJobQueue("succeed")
    with api_client(queue) as client:
        response = client.post(ANALYZE, files=_pdf_upload(), headers=auth())
    assert response.status_code == 200
    body = response.json()
    assert body["form"] == sample()
    jsonschema.Draft202012Validator(official_schema()).validate(body["form"])
    assert "null" not in json.dumps(body["form"])
    assert queue.wait_calls == [60]
    # Treść pliku trafiła do kolejki bez zmian.
    assert list(queue.files.values()) == [pdf()]


def test_analyze_returns_202_when_not_finished() -> None:
    with api_client(FakeJobQueue("pending")) as client:
        response = client.post(ANALYZE, files=_pdf_upload(), headers=auth())
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "QUEUED"
    assert body["filename"] == "wniosek.pdf"
    assert response.headers["location"] == body["status_url"] == f"/api/v1/jobs/{body['job_id']}"
    assert response.headers["retry-after"] == "5"


def test_analyze_wait_zero_skips_waiting() -> None:
    queue = FakeJobQueue("succeed")
    with api_client(queue) as client:
        response = client.post(f"{ANALYZE}?wait=0", files=_pdf_upload(), headers=auth())
    assert response.status_code == 202
    assert queue.wait_calls == []


def test_analyze_wait_capped_by_max(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANALYZE_MAX_WAIT_SECONDS", "120")
    queue = FakeJobQueue("pending")
    with api_client(queue) as client:
        client.post(f"{ANALYZE}?wait=9999", files=_pdf_upload(), headers=auth())
    assert queue.wait_calls == [120]


@pytest.mark.parametrize(
    ("code", "status"),
    [
        (JobErrorCode.PIPELINE_NOT_IMPLEMENTED, 501),
        (JobErrorCode.DOCUMENT_UNREADABLE, 422),
        (JobErrorCode.PIPELINE_ERROR, 500),
    ],
)
def test_analyze_failed_job(code: JobErrorCode, status: int) -> None:
    with api_client(FakeJobQueue("fail", error_code=code)) as client:
        response = client.post(ANALYZE, files=_pdf_upload(), headers=auth())
    assert response.status_code == status
    error = response.json()["error"]
    assert error["code"] == code
    assert len(error["details"]["job_id"]) == 32


def test_queue_unavailable_returns_503() -> None:
    with api_client(FakeJobQueue("unavailable")) as client:
        response = client.post(ANALYZE, files=_pdf_upload(), headers=auth())
    assert response.status_code == 503
    assert response.headers["retry-after"] == "5"


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
    queue = FakeJobQueue()
    with api_client(queue) as client:
        response = client.post(
            ANALYZE, files={"file": (name, content, "application/pdf")}, headers=auth()
        )
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert queue.files == {}


def test_analyze_rejects_too_large_file(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_FILE_SIZE_MB", "1")
    big = pdf() + b"0" * (1024 * 1024)
    with api_client(FakeJobQueue()) as client:
        response = client.post(
            ANALYZE, files={"file": ("duzy.pdf", big, "application/pdf")}, headers=auth()
        )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "FILE_TOO_LARGE"


def test_request_body_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_FILE_SIZE_MB", "1")
    monkeypatch.setenv("MAX_BATCH_FILES", "1")
    huge = b"0" * (3 * 1024 * 1024)
    with api_client(FakeJobQueue()) as client:
        response = client.post(
            ANALYZE, files={"file": ("duzy.pdf", huge, "application/pdf")}, headers=auth()
        )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "REQUEST_TOO_LARGE"


def test_analyze_requires_file() -> None:
    with api_client(FakeJobQueue()) as client:
        response = client.post(ANALYZE, headers=auth())
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "REQUEST_INVALID"


# ------------------------------------------------------------------ batch


def test_batch_accepts_valid_and_reports_rejected() -> None:
    queue = FakeJobQueue("pending")
    files = [
        ("files", ("a.pdf", pdf(), "application/pdf")),
        ("files", ("b.png", png(), "image/png")),
        ("files", ("c.exe", b"MZ", "application/octet-stream")),
        ("files", ("d.png", pdf(), "image/png")),
    ]
    with api_client(queue) as client:
        response = client.post(BATCH, files=files, headers=auth())
    assert response.status_code == 202
    body = response.json()
    assert [j["filename"] for j in body["jobs"]] == ["a.pdf", "b.png"]
    assert [(r["filename"], r["code"]) for r in body["rejected"]] == [
        ("c.exe", "EXTENSION_NOT_ALLOWED"),
        ("d.png", "MIME_MISMATCH"),
    ]
    assert len(queue.files) == 2


def test_batch_all_rejected() -> None:
    with api_client(FakeJobQueue()) as client:
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
    queue = FakeJobQueue()
    with api_client(queue) as client:
        response = client.post(BATCH, files=files, headers=auth())
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "TOO_MANY_FILES"
    assert queue.files == {}


# ------------------------------------------------------------------ jobs


def test_job_status_lifecycle_and_progress() -> None:
    queue = FakeJobQueue("in_progress")
    with api_client(queue) as client:
        job_id = client.post(f"{ANALYZE}?wait=0", files=_pdf_upload(), headers=auth()).json()[
            "job_id"
        ]
        response = client.get(f"/api/v1/jobs/{job_id}", headers=auth())
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "IN_PROGRESS"
    assert body["progress"] == {"stage": "text_extraction", "step": 2, "total_steps": 10}
    assert "result" not in body


def test_job_succeeded_contains_result() -> None:
    with api_client(FakeJobQueue("succeed")) as client:
        job_id = client.post(f"{ANALYZE}?wait=0", files=_pdf_upload(), headers=auth()).json()[
            "job_id"
        ]
        body = client.get(f"/api/v1/jobs/{job_id}", headers=auth()).json()
    assert body["status"] == "SUCCEEDED"
    assert body["result"]["form"] == sample()


def test_job_of_other_user_is_not_found() -> None:
    queue = FakeJobQueue()
    job_id = queue.add_foreign_job(owner="ktos-inny")
    with api_client(queue) as client:
        response = client.get(f"/api/v1/jobs/{job_id}", headers=auth("urzednik"))
        assert client.get(f"/api/v1/jobs/{job_id}", headers=auth("ktos-inny")).status_code == 200
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "JOB_NOT_FOUND"


def test_unknown_and_malformed_job_ids() -> None:
    with api_client(FakeJobQueue()) as client:
        assert client.get(f"/api/v1/jobs/{'a' * 32}", headers=auth()).status_code == 404
        response = client.get("/api/v1/jobs/../../etc", headers=auth())
        assert response.status_code in {404, 422}
        assert client.get("/api/v1/jobs/XYZ", headers=auth()).status_code == 422
