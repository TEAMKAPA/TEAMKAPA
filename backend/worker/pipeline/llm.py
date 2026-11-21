"""Klient LLM dla etapów 06–08 (#8) skonfigurowany z ``Settings`` i klasyfikacja jego błędów.

Klient z #8 jest synchroniczny (urllib), dlatego orkiestrator wywołuje etapy w wątku
(``asyncio.to_thread``). Anulowanie zadania nie przerywa trwającego wywołania HTTP —
ogranicza je ``LLM_TIMEOUT_SECONDS``.

Tryb demonstracyjny (``LLM_DEMO_MODE``) włącza deterministyczne odpowiedzi testowe z #8;
w każdym innym przypadku wymagany jest prawdziwy model (``LLM_MODEL``).
"""

import asyncio
import logging
from collections.abc import Callable
from urllib.error import HTTPError, URLError

from backend.shared.config import Settings
from backend.worker.clients.llm_client import LLMClient, LLMConfig, StructuredOutputError
from backend.worker.pipeline.errors import PipelineStageError, PipelineTemporarilyUnavailableError

logger = logging.getLogger(__name__)

DEMO_MODEL_NAME = "tryb-demo (bez modelu)"


def build_llm_client(settings: Settings) -> LLMClient:
    if settings.llm_demo_mode:
        return LLMClient(LLMConfig(offline=True))
    if not settings.llm_model or settings.llm_model.startswith("change-me"):
        raise PipelineStageError(
            "konfiguracja modelu",
            "nie ustawiono LLM_MODEL (identyfikator modelu w Eden AI, np. openai/gpt-4o-mini)",
        )
    api_key = settings.edenai_api_key.get_secret_value().strip() if settings.edenai_api_key else ""
    if not api_key or api_key.lower().startswith("change-me"):
        raise PipelineStageError(
            "konfiguracja modelu",
            "nie ustawiono EDENAI_API_KEY (klucz API Eden AI)",
        )
    return LLMClient(
        LLMConfig(
            model=settings.llm_model,
            api_key=api_key,
            base_url=settings.edenai_base_url.rstrip("/"),
            timeout_seconds=float(settings.llm_timeout_seconds),
            offline=False,
        )
    )


def model_name(settings: Settings) -> str:
    return DEMO_MODEL_NAME if settings.llm_demo_mode else str(settings.llm_model)


def _is_transient(exc: StructuredOutputError) -> bool:
    cause = exc.__cause__
    if isinstance(cause, HTTPError):
        return cause.code >= 500 or cause.code == 429  # serwer przeciążony / ładuje model
    return isinstance(cause, URLError | TimeoutError | ConnectionError)


async def run_llm_stage[T](
    stage: str, function: Callable[..., T], *args: object, **kwargs: object
) -> T:
    """Uruchamia etap #8 w wątku; błędy zamienia na wyjątki pipeline'u.

    - serwer niedostępny / timeout / 5xx → ``PipelineTemporarilyUnavailableError`` (ponowienie),
    - odpowiedź niezgodna ze schematem po ponowieniach klienta, błąd 4xx → ``PipelineStageError``,
    - etap odrzucił wynik (``ValueError``, np. struktura niezgodna z COI) → ``PipelineStageError``.
    """
    try:
        return await asyncio.to_thread(function, *args, **kwargs)
    except StructuredOutputError as exc:
        if _is_transient(exc):
            raise PipelineTemporarilyUnavailableError(
                f"serwer modelu językowego niedostępny (etap: {stage})"
            ) from exc
        logger.warning("Etap %s: odpowiedź modelu odrzucona: %s", stage, exc)
        raise PipelineStageError(
            stage, "model językowy nie zwrócił poprawnej odpowiedzi zgodnej ze schematem"
        ) from exc
    except ValueError as exc:
        raise PipelineStageError(stage, str(exc)[:500]) from exc
