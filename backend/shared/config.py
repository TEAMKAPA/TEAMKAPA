"""Konfiguracja aplikacji ładowana wyłącznie w runtime (zmienne środowiskowe / Docker secrets).

Jedno źródło prawdy dla API i workera. Sekrety nigdy nie mają wartości domyślnych —
brak lub słaby sekret kończy start procesu czytelnym błędem (fail fast).
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Docker secrets montowane są jako pliki w /run/secrets/<nazwa_zmiennej>.
_DOCKER_SECRETS_DIR = Path("/run/secrets")

# Wartości z .env.example, których nie wolno użyć w działającym systemie.
_PLACEHOLDER_SECRETS = frozenset({"change-me", "change-me-in-runtime", "changeme", "secret"})

_MIN_JWT_SECRET_LENGTH = 32


class Settings(BaseSettings):
    """Ustawienia wspólne dla API i workera."""

    model_config = SettingsConfigDict(
        env_file=None,  # w kontenerze zmienne podaje docker compose; lokalnie export / .env w IDE
        secrets_dir=_DOCKER_SECRETS_DIR if _DOCKER_SECRETS_DIR.is_dir() else None,
        case_sensitive=False,
        extra="ignore",
        frozen=True,
    )

    # --- pliki wejściowe ---
    max_file_size_mb: int = Field(default=20, ge=1, le=500)
    temp_dir: Path | None = Field(
        default=None,
        description="Katalog plików tymczasowych; None = katalog systemowy (tmpfs w kontenerze).",
    )

    # --- infrastruktura ---
    database_url: SecretStr | None = Field(
        default=None, description="DSN PostgreSQL, np. postgresql://user:pass@postgres:5432/db"
    )
    valkey_url: str = "redis://valkey:6379/0"

    # --- lokalne modele (endpointy zgodne z OpenAI API: vLLM / Ollama / llama.cpp) ---
    llm_base_url: str = "http://llm:8000/v1"
    llm_model: str | None = None
    vlm_base_url: str = "http://vlm:8001/v1"
    vlm_model: str | None = None

    # --- przetwarzanie ---
    accuracy_mode: Literal["max", "balanced"] = "max"
    render_dpi: int = Field(default=200, ge=72, le=600)

    @property
    def max_file_size_bytes(self) -> int:
        return self.max_file_size_mb * 1024 * 1024


class ApiSettings(Settings):
    """Ustawienia API — dodatkowo uwierzytelnianie JWT (tylko weryfikacja tokenów)."""

    jwt_enabled: bool = True
    jwt_secret_key: SecretStr | None = None
    jwt_algorithm: Literal["HS256", "HS384", "HS512"] = "HS256"
    jwt_expire_minutes: int = Field(default=60, ge=1, le=24 * 60)

    @field_validator("jwt_secret_key")
    @classmethod
    def _reject_placeholder(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and value.get_secret_value().strip().lower() in _PLACEHOLDER_SECRETS:
            raise ValueError("JWT_SECRET_KEY ma wartość przykładową z .env.example — ustaw własny")
        return value

    @model_validator(mode="after")
    def _require_secret_when_enabled(self) -> ApiSettings:
        if not self.jwt_enabled:
            return self
        if self.jwt_secret_key is None:
            raise ValueError("JWT_ENABLED=true wymaga ustawienia JWT_SECRET_KEY")
        if len(self.jwt_secret_key.get_secret_value()) < _MIN_JWT_SECRET_LENGTH:
            raise ValueError(
                f"JWT_SECRET_KEY musi mieć co najmniej {_MIN_JWT_SECRET_LENGTH} znaki "
                '(wygeneruj: python -c "import secrets; print(secrets.token_urlsafe(48))")'
            )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


@lru_cache(maxsize=1)
def get_api_settings() -> ApiSettings:
    return ApiSettings()
