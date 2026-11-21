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
    db_pool_max_size: int = Field(default=5, ge=1, le=50)
    valkey_url: str = "redis://valkey:6379/0"

    # --- kolejka zadań ---
    job_queue_ttl_seconds: int = Field(
        default=3600,
        ge=60,
        le=24 * 3600,
        description="Jak długo plik czeka w pamięci Valkey na workera (łącznie z ponowieniami); "
        "po tym czasie zadanie wygasa, a plik jest usuwany.",
    )
    job_result_ttl_seconds: int = Field(
        default=3600,
        ge=60,
        le=7 * 24 * 3600,
        description="Jak długo arq trzyma wynik w Valkey (źródłem prawdy jest PostgreSQL).",
    )
    job_timeout_seconds: int = Field(
        default=1800, ge=10, le=6 * 3600, description="Maksymalny czas analizy jednego pliku."
    )
    worker_max_jobs: int = Field(
        default=1, ge=1, le=32, description="Liczba równoległych analiz w jednym workerze."
    )
    max_job_tries: int = Field(
        default=3,
        ge=1,
        le=10,
        description="Ile razy łącznie można uruchomić analizę pliku (ponowienia po błędach "
        "przejściowych i awariach workera).",
    )
    retry_backoff_seconds: int = Field(
        default=30,
        ge=0,
        le=3600,
        description="Opóźnienie pierwszego ponowienia; kolejne rosną wykładniczo (×2).",
    )
    result_retention_days: int = Field(
        default=30,
        ge=1,
        le=3650,
        description="Po ilu dniach usuwane są zadania i wygenerowane wyniki "
        "(zatwierdzone formularze zostają).",
    )
    stale_job_grace_seconds: int = Field(
        default=120,
        ge=10,
        le=3600,
        description="Po jakim czasie bez śladu w kolejce zadanie uznaje się za utracone.",
    )

    # --- lokalne modele (endpointy zgodne z OpenAI API: vLLM / Ollama / llama.cpp) ---
    llm_base_url: str = "http://llm:8000/v1"
    llm_model: str | None = None
    vlm_base_url: str = "http://vlm:8001/v1"
    vlm_model: str | None = None

    # --- przetwarzanie ---
    accuracy_mode: Literal["max", "balanced"] = "max"
    render_dpi: int = Field(default=200, ge=72, le=600)

    # --- progi pewności pól (kolory w UI); do kalibracji na zbiorze testowym ---
    confidence_high: float = Field(default=0.85, ge=0.0, le=1.0)
    confidence_low: float = Field(default=0.5, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _thresholds_ordered(self) -> Settings:
        if self.confidence_low > self.confidence_high:
            raise ValueError("CONFIDENCE_LOW nie może być większy niż CONFIDENCE_HIGH")
        return self

    @property
    def max_file_size_bytes(self) -> int:
        return self.max_file_size_mb * 1024 * 1024


class ApiSettings(Settings):
    """Ustawienia API — dodatkowo uwierzytelnianie JWT (tylko weryfikacja tokenów)."""

    jwt_enabled: bool = True
    jwt_secret_key: SecretStr | None = None
    jwt_algorithm: Literal["HS256", "HS384", "HS512"] = "HS256"
    jwt_expire_minutes: int = Field(default=60, ge=1, le=24 * 60)
    jwt_leeway_seconds: int = Field(
        default=30, ge=0, le=300, description="Tolerancja różnicy zegarów przy sprawdzaniu exp."
    )

    # --- endpointy analizy ---
    analyze_wait_seconds: int = Field(
        default=60,
        ge=0,
        description="Ile POST /forms/analyze czeka na wynik, zanim zwróci 202 z job_id.",
    )
    analyze_max_wait_seconds: int = Field(
        default=300, ge=0, le=900, description="Górny limit parametru ?wait= (proxy ma 300 s)."
    )
    max_batch_files: int = Field(default=20, ge=1, le=200)

    @property
    def max_request_bytes(self) -> int:
        """Limit całego żądania multipart: pełna paczka plików + zapas na nagłówki części."""
        return self.max_file_size_bytes * self.max_batch_files + 1024 * 1024

    @model_validator(mode="after")
    def _wait_within_limit(self) -> ApiSettings:
        if self.analyze_wait_seconds > self.analyze_max_wait_seconds:
            raise ValueError("ANALYZE_WAIT_SECONDS nie może przekraczać ANALYZE_MAX_WAIT_SECONDS")
        return self

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
