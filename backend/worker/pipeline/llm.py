"""Model językowy dla etapów 06–08 (#8): Eden AI przez ``ModelApiClient`` + klasyfikacja błędów.

Klient z #8 (``LLMClient``) pozostaje bez zmian — dostaje transport HTTP z ``ModelApiClient``
(Eden AI, tryb JSON, zero data retention, zliczanie tokenów i kosztu). Przed pierwszym
wywołaniem sprawdzamy w katalogu Eden AI, że model ma otwarte wagi.

Wywołania są synchroniczne — orkiestrator uruchamia je w wątku (``asyncio.to_thread``);
anulowanie zadania nie przerywa trwającego wywołania HTTP (ogranicza je
``LLM_TIMEOUT_SECONDS``).

Tryb demonstracyjny (``LLM_DEMO_MODE``) włącza deterministyczne odpowiedzi testowe z #8.
"""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass

from backend.shared.config import Settings
from backend.worker.clients.llm_client import LLMClient, LLMConfig, StructuredOutputError
from backend.worker.clients.model_api import (
    ModelApiClient,
    ModelInfo,
    ModelNotAllowedError,
    ModelRequestRejectedError,
    ModelServiceUnavailableError,
    Usage,
    ensure_open_weights,
)
from backend.worker.pipeline.errors import PipelineStageError, PipelineTemporarilyUnavailableError

logger = logging.getLogger(__name__)

DEMO_MODEL_NAME = "tryb-demo (bez modelu)"
CONFIG_STAGE = "konfiguracja modelu"


@dataclass(frozen=True, slots=True)
class LLMSession:
    """Klient LLM dla etapów 06–08 i licznik zużycia (None w trybie demo)."""

    client: LLMClient
    usage: Usage | None = None
    model: ModelInfo | None = None


def build_llm_session(settings: Settings) -> LLMSession:
    if settings.llm_demo_mode:
        return LLMSession(LLMClient(LLMConfig(offline=True)))
    api_key = settings.llm_api_key.get_secret_value() if settings.llm_api_key else None
    try:
        info = ensure_open_weights(settings.llm_base_url, settings.llm_model, api_key=api_key)
    except (ModelNotAllowedError, ModelRequestRejectedError) as exc:
        raise PipelineStageError(CONFIG_STAGE, str(exc)) from exc
    except ModelServiceUnavailableError as exc:
        raise PipelineTemporarilyUnavailableError("katalog modeli Eden AI niedostępny") from exc
    api = ModelApiClient(
        base_url=settings.llm_base_url,
        model=settings.llm_model,
        api_key=api_key,
        timeout_seconds=float(settings.llm_timeout_seconds),
        max_tokens=settings.llm_max_tokens,
        zero_data_retention=settings.llm_zero_data_retention,
        model_info=info,
    )
    client = LLMClient(
        LLMConfig(
            model=settings.llm_model,
            offline=False,
            timeout_seconds=float(settings.llm_timeout_seconds),
        ),
        transport=api.llm_transport,
    )
    return LLMSession(client=client, usage=api.usage, model=info)


def model_name(settings: Settings) -> str:
    return DEMO_MODEL_NAME if settings.llm_demo_mode else settings.llm_model


async def run_llm_stage[T](
    stage: str, function: Callable[..., T], *args: object, **kwargs: object
) -> T:
    """Uruchamia etap #8 w wątku; błędy zamienia na wyjątki pipeline'u.

    - usługa modelu niedostępna (sieć, timeout, 429, 5xx) → ponowienie zadania,
    - zapytanie odrzucone (klucz, środki, model) → ``PipelineStageError`` z przyczyną,
    - odpowiedź niezgodna ze schematem po ponowieniach klienta → ``PipelineStageError``,
    - etap odrzucił wynik (``ValueError``, np. struktura niezgodna z COI) → ``PipelineStageError``.
    """
    try:
        return await asyncio.to_thread(function, *args, **kwargs)
    except ModelServiceUnavailableError as exc:
        raise PipelineTemporarilyUnavailableError(
            f"usługa modelu językowego niedostępna (etap: {stage}): {exc}"
        ) from exc
    except ModelRequestRejectedError as exc:
        raise PipelineStageError(stage, str(exc)) from exc
    except StructuredOutputError as exc:
        logger.warning("Etap %s: odpowiedź modelu odrzucona: %s", stage, exc)
        raise PipelineStageError(
            stage, "model językowy nie zwrócił poprawnej odpowiedzi zgodnej ze schematem"
        ) from exc
    except ValueError as exc:
        raise PipelineStageError(stage, str(exc)[:500]) from exc
