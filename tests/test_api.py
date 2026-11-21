from fastapi.testclient import TestClient

from backend.api.main import API_VERSION, app


def test_health_ok() -> None:
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": API_VERSION}


def test_openapi_available() -> None:
    with TestClient(app) as client:
        response = client.get("/openapi.json")
    assert response.status_code == 200
    assert response.json()["info"]["title"] == "COI Cyfryzacja formularzy"
