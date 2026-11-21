from collections.abc import Iterator

import pytest

from backend.shared.config import get_api_settings, get_settings

TEST_JWT_SECRET = "test-secret-" + "x" * 40


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _test_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Każdy test startuje z poprawną, izolowaną konfiguracją i pustym cache ustawień."""
    monkeypatch.setenv("JWT_ENABLED", "true")
    monkeypatch.setenv("JWT_SECRET_KEY", TEST_JWT_SECRET)
    for var in ("EDENAI_API_KEY", "EDENAI_BASE_URL", "LLM_MODEL", "LLM_DEMO_MODE"):
        monkeypatch.delenv(var, raising=False)
    get_settings.cache_clear()
    get_api_settings.cache_clear()
    yield
    get_settings.cache_clear()
    get_api_settings.cache_clear()
