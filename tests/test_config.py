import pytest
from pydantic import ValidationError

from backend.shared.config import ApiSettings, Settings


def test_defaults_match_documentation() -> None:
    settings = Settings()
    assert settings.max_file_size_mb == 20
    assert settings.max_file_size_bytes == 20 * 1024 * 1024
    assert settings.accuracy_mode == "max"
    assert settings.render_dpi == 200
    assert settings.edenai_base_url == "https://api.edenai.run/v3"
    assert settings.edenai_api_key is None


def test_edenai_api_key_is_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EDENAI_API_KEY", "fake-edenai-token")
    settings = Settings()
    assert settings.edenai_api_key is not None
    assert settings.edenai_api_key.get_secret_value() == "fake-edenai-token"
    assert "fake-edenai-token" not in repr(settings)


def test_valid_api_settings() -> None:
    settings = ApiSettings()
    assert settings.jwt_algorithm == "HS256"
    assert settings.jwt_secret_key is not None
    assert "test-secret" not in repr(settings)  # sekret nie wycieka do logów


def test_missing_secret_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JWT_SECRET_KEY")
    with pytest.raises(ValidationError, match="wymaga ustawienia JWT_SECRET_KEY"):
        ApiSettings()


def test_short_secret_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_SECRET_KEY", "too-short")
    with pytest.raises(ValidationError, match="co najmniej 32"):
        ApiSettings()


@pytest.mark.parametrize("placeholder", ["change-me-in-runtime", "CHANGE-ME", "changeme"])
def test_placeholder_secret_rejected(monkeypatch: pytest.MonkeyPatch, placeholder: str) -> None:
    monkeypatch.setenv("JWT_SECRET_KEY", placeholder)
    with pytest.raises(ValidationError, match="wartość przykładową"):
        ApiSettings()


def test_jwt_can_be_disabled_for_local_dev(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_ENABLED", "false")
    monkeypatch.delenv("JWT_SECRET_KEY")
    assert ApiSettings().jwt_enabled is False


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("MAX_FILE_SIZE_MB", "0"),
        ("ACCURACY_MODE", "fast"),
        ("RENDER_DPI", "10"),
        ("JWT_ALGORITHM", "none"),
    ],
)
def test_invalid_values_rejected(monkeypatch: pytest.MonkeyPatch, name: str, value: str) -> None:
    monkeypatch.setenv(name, value)
    with pytest.raises(ValidationError):
        ApiSettings()
