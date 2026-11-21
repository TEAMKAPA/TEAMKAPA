from collections.abc import Iterator

import pytest

from backend.shared.config import get_api_settings, get_settings

TEST_JWT_SECRET = "test-secret-" + "x" * 40


@pytest.fixture(autouse=True)
def _test_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Każdy test startuje z poprawną, izolowaną konfiguracją i pustym cache ustawień."""
    monkeypatch.setenv("JWT_ENABLED", "true")
    monkeypatch.setenv("JWT_SECRET_KEY", TEST_JWT_SECRET)
    get_settings.cache_clear()
    get_api_settings.cache_clear()
    yield
    get_settings.cache_clear()
    get_api_settings.cache_clear()
