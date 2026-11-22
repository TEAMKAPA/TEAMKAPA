"""Konfiguracja VLM (#11): backend, model z listy dozwolonych, limity i parametry generowania.

Wczytywana przez pydantic-settings ze zmiennych środowiskowych i z pliku ``.env`` w katalogu
głównym repozytorium (lokalnie); **zmienna środowiskowa ma pierwszeństwo przed plikiem**.
W kontenerze pliku ``.env`` nie ma, a zmienne podaje docker compose. Token Eden AI
(``EDENAI_API_KEY``) jest ``SecretStr``: nie trafia do ``repr``, logów, błędów ani raportów.

Jedynym celem połączeń zewnętrznych jest skonfigurowany adres Eden AI (tylko ``https``); backend
lokalny łączy się z serwerem modelu w sieci wewnętrznej (vLLM lub llama.cpp, API zgodne z OpenAI).
"""

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Annotated
from urllib.parse import urlparse

from pydantic import AliasChoices, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[4]
ENV_FILE = REPO_ROOT / ".env"
DEFAULT_CACHE_DIR = REPO_ROOT / "dane" / "robocze" / "vlm_cache"


@dataclass(frozen=True)
class ModelInfo:
    weights: str  # identyfikator wag (Hugging Face)
    license: str  # licencja wag (docs/licenses.md)


EDENAI_MODELS: dict[str, ModelInfo] = {
    "deepinfra/Qwen/Qwen3-VL-30B-A3B-Instruct": ModelInfo(
        "Qwen/Qwen3-VL-30B-A3B-Instruct", "Apache-2.0"
    ),
}
"""Modele open-weight o licencji permisywnej dopuszczone w Eden AI (brief 7.4, docs/licenses.md)."""

LOCAL_MODELS: dict[str, ModelInfo] = {
    "Qwen/Qwen3-VL-30B-A3B-Instruct": ModelInfo("Qwen/Qwen3-VL-30B-A3B-Instruct", "Apache-2.0"),
}
"""Te same wagi uruchomione lokalnie (vLLM, llama.cpp); wagi pobiera człowiek."""


class VlmBackend(StrEnum):
    EDENAI = "edenai"
    LOCAL = "local"
    FAKE = "fake"  # testy i ewaluacja bez sieci (nagrane odpowiedzi)


class AccuracyMode(StrEnum):
    MAX = "max"  # podwójny odczyt każdej strony
    BALANCED = "balanced"  # podwójny odczyt tylko stron z niską pewnością


class ResponseFormat(StrEnum):
    JSON_SCHEMA = "json_schema"  # wymuszony schemat (structured output)
    JSON_OBJECT = "json_object"  # tylko poprawny JSON; schemat w prompcie i walidacja Pydantic


Seconds = Annotated[float, Field(gt=0, le=1800)]


class VlmSettings(BaseSettings):
    """Zmienne ``VLM_*`` oraz ``EDENAI_API_KEY`` i ``EDENAI_BASE_URL`` (wspólne z LLM tekstowym)."""

    model_config = SettingsConfigDict(
        env_prefix="VLM_",
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        frozen=True,
        populate_by_name=True,
    )

    backend: VlmBackend = VlmBackend.EDENAI
    edenai_api_key: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("EDENAI_API_KEY", "VLM_EDENAI_API_KEY")
    )
    edenai_base_url: str = Field(
        default="https://api.edenai.run/v3",
        validation_alias=AliasChoices("EDENAI_BASE_URL", "VLM_EDENAI_BASE_URL"),
    )
    edenai_model: str = "deepinfra/Qwen/Qwen3-VL-30B-A3B-Instruct"
    local_base_url: str = Field(
        default="http://vlm:8001/v1",
        validation_alias=AliasChoices("VLM_BASE_URL", "VLM_LOCAL_BASE_URL"),
    )
    local_model: str = Field(
        default="Qwen/Qwen3-VL-30B-A3B-Instruct",
        validation_alias=AliasChoices("VLM_MODEL", "VLM_LOCAL_MODEL"),
    )

    timeout_s: Seconds = 120.0  # limit jednego wywołania
    transport_retries: int = Field(default=1, ge=0, le=5)  # ponowienia po błędzie sieci/5xx/429
    retry_backoff_s: float = Field(default=2.0, ge=0, le=60)
    temperature: float = Field(default=0.0, ge=0, le=2)
    seed: int = 1234
    send_seed: bool = False  # Qwen3-VL w Eden AI nie ma ``seed`` w supported_parameters
    max_tokens: int = Field(default=4096, ge=64, le=16384)
    response_format: ResponseFormat = ResponseFormat.JSON_SCHEMA
    image_max_side_px: int = Field(default=1600, ge=256, le=4096)
    draw_marks: bool = True  # numery elementów cv-* na obrazie (tryb 1)
    max_added_per_page: int = Field(default=10, ge=0, le=100)
    accuracy_mode: AccuracyMode = Field(
        default=AccuracyMode.MAX,
        validation_alias=AliasChoices("ACCURACY_MODE", "VLM_ACCURACY_MODE"),
    )
    cache_dir: Path = DEFAULT_CACHE_DIR
    cache_enabled: bool = True

    @property
    def model(self) -> str:
        return self.local_model if self.backend is VlmBackend.LOCAL else self.edenai_model

    @property
    def base_url(self) -> str:
        return self.local_base_url if self.backend is VlmBackend.LOCAL else self.edenai_base_url

    @model_validator(mode="after")
    def _allowed(self) -> VlmSettings:
        if self.backend is VlmBackend.EDENAI:
            if self.edenai_model not in EDENAI_MODELS:
                raise ValueError(
                    f"model {self.edenai_model!r} spoza listy dozwolonych modeli open-weight "
                    "(backend/worker/vision/vlm/config.py, docs/licenses.md)"
                )
            url = urlparse(self.edenai_base_url)
            if url.scheme != "https" or not url.hostname:
                raise ValueError("EDENAI_BASE_URL musi być adresem https")
        if self.backend is VlmBackend.LOCAL:
            if self.local_model not in LOCAL_MODELS:
                raise ValueError(f"model lokalny {self.local_model!r} spoza listy dozwolonych")
            if urlparse(self.local_base_url).scheme not in {"http", "https"}:
                raise ValueError("VLM_BASE_URL musi być adresem http(s)")
        return self

    def api_key_length(self) -> int:
        """Długość tokenu (do kontroli wczytania; wartość nigdy nie jest wypisywana)."""
        return len(self.edenai_api_key.get_secret_value()) if self.edenai_api_key else 0


def load_vlm_settings(**overrides: object) -> VlmSettings:
    return VlmSettings(**overrides)  # type: ignore[arg-type]
