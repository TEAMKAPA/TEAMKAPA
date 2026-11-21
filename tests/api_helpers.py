"""Fabryka klienta testowego API z kolejką i magazynem zadań w pamięci."""

from collections.abc import Iterator
from contextlib import contextmanager

from fastapi.testclient import TestClient

from backend.api.main import create_app
from backend.api.routes import get_mime_detector
from backend.api.security import issue_token
from backend.shared.config import ApiSettings, get_api_settings
from tests.fakes import FakeJobQueue, FakeJobStore, sample_result
from tests.file_samples import sniff_mime

__all__ = ["FakeJobQueue", "FakeJobStore", "api_client", "auth", "sample_result"]


@contextmanager
def api_client(queue: FakeJobQueue) -> Iterator[TestClient]:
    async def queue_factory(_: ApiSettings) -> FakeJobQueue:
        return queue

    async def store_factory(_: ApiSettings) -> FakeJobStore:
        return queue.store

    app = create_app(queue_factory=queue_factory, store_factory=store_factory)
    app.dependency_overrides[get_mime_detector] = lambda: sniff_mime
    with TestClient(app) as client:
        yield client


def auth(subject: str = "urzednik") -> dict[str, str]:
    return {"Authorization": f"Bearer {issue_token(get_api_settings(), subject)}"}
