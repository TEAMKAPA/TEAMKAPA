"""Klient API modeli językowych zgodnego z OpenAI — Eden AI v3 (``/v3/chat/completions``).

Zasady zgodności z regulaminem (doprecyzowanie mentorów: wolno wołać przez API wyłącznie
modele z otwartymi wagami, możliwe do postawienia na własnej infrastrukturze):
- ``ensure_open_weights`` sprawdza w ``/v3/models``, że model ma ``open_weights: true``,
  i blokuje analizę dla modeli zamkniętych,
- zapytanie zawsze wskazuje konkretny model ``dostawca/model`` i nie zawiera ``fallbacks`` —
  Eden AI nie przełączy go na inny (np. zamknięty) model,
- ``zero_data_retention``: tylko dostawcy, którzy nie zapisują zapytań i odpowiedzi.

Klient jest synchroniczny (stdlib ``urllib``) — wołany w wątku przez orkiestrator.
Kanał wizualny (#11) może wysyłać obrazy: ``{"type": "image_url", "image_url": {"url":
"data:image/png;base64,..."}}`` w ``content`` wiadomości.
"""

import json
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request

logger = logging.getLogger(__name__)


class ModelServiceUnavailableError(RuntimeError):
    """Usługa modelu chwilowo niedostępna (sieć, timeout, 429, 5xx) — warto ponowić."""


class ModelRequestRejectedError(RuntimeError):
    """Zapytanie odrzucone na stałe (klucz, środki, nieznany model, błędne parametry)."""


class ModelNotAllowedError(RuntimeError):
    """Model nie ma otwartych wag — niedozwolony w rozwiązaniu."""


@dataclass(frozen=True, slots=True)
class ModelInfo:
    model_id: str
    hugging_face_id: str | None
    input_cost_per_token: float
    output_cost_per_token: float
    supports_system_messages: bool = True


@dataclass(slots=True)
class Usage:
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add(self, prompt: int, completion: int, model: ModelInfo | None) -> None:
        with self._lock:
            self.calls += 1
            self.prompt_tokens += prompt
            self.completion_tokens += completion
            if model is not None:
                self.cost_usd += (
                    prompt * model.input_cost_per_token + completion * model.output_cost_per_token
                )

    def as_dict(self) -> dict[str, float]:
        return {
            "llm_calls": self.calls,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "cost_usd": round(self.cost_usd, 6),
        }


_REJECTED_MESSAGES = {
    401: "nieprawidłowy klucz API Eden AI",
    402: "brak środków na koncie Eden AI",
    403: "brak dostępu do modelu w Eden AI",
    404: "model nie istnieje w Eden AI",
}

Opener = Callable[..., Any]


def _request(
    url: str,
    *,
    payload: dict[str, Any] | None,
    api_key: str | None,
    timeout: float,
    opener: Opener,
) -> dict[str, Any]:
    if not url.startswith(("http://", "https://")):
        raise ModelRequestRejectedError(f"Nieobsługiwany adres usługi modelu: {url!r}")
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib_request.Request(  # noqa: S310 — schemat sprawdzony wyżej
        url, data=data, headers=headers, method="POST" if payload is not None else "GET"
    )
    try:
        with opener(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib_error.HTTPError as exc:
        if exc.code == 429 or exc.code >= 500:
            raise ModelServiceUnavailableError(f"usługa modelu odpowiedziała {exc.code}") from exc
        reason = _REJECTED_MESSAGES.get(exc.code, f"zapytanie odrzucone (HTTP {exc.code})")
        raise ModelRequestRejectedError(reason) from exc
    except (urllib_error.URLError, TimeoutError, ConnectionError) as exc:
        raise ModelServiceUnavailableError("brak połączenia z usługą modelu") from exc
    except json.JSONDecodeError as exc:
        raise ModelServiceUnavailableError("nieprawidłowa odpowiedź usługi modelu") from exc
    if not isinstance(body, dict):
        raise ModelServiceUnavailableError("nieprawidłowa odpowiedź usługi modelu")
    return body


@lru_cache(maxsize=16)
def _model_catalog(base_url: str, api_key: str | None, timeout: float) -> dict[str, Any]:
    body = _request(
        f"{base_url.rstrip('/')}/models",
        payload=None,
        api_key=api_key,
        timeout=timeout,
        opener=urllib_request.urlopen,
    )
    return {str(m.get("id")): m for m in body.get("data") or [] if isinstance(m, dict)}


def ensure_open_weights(
    base_url: str,
    model_id: str,
    *,
    api_key: str | None = None,
    timeout: float = 30.0,
    catalog: Callable[[str, str | None, float], dict[str, Any]] = _model_catalog,
) -> ModelInfo:
    """Zwraca dane modelu z katalogu Eden AI albo zgłasza ``ModelNotAllowedError``."""
    if "/" not in model_id:
        raise ModelNotAllowedError(
            f"Model „{model_id}” musi mieć postać 'dostawca/model' — bez dostawcy Eden AI "
            "sam wybiera model i może użyć zamkniętego"
        )
    entry = catalog(base_url, api_key, timeout).get(model_id)
    if entry is None:
        raise ModelNotAllowedError(f"Model „{model_id}” nie występuje w katalogu Eden AI")
    if entry.get("open_weights") is not True:
        raise ModelNotAllowedError(
            f"Model „{model_id}” nie ma otwartych wag — regulamin dopuszcza wyłącznie modele "
            "otwarte, możliwe do postawienia na własnej infrastrukturze"
        )
    pricing = entry.get("pricing") or {}
    capabilities = entry.get("capabilities") or {}
    return ModelInfo(
        model_id=model_id,
        hugging_face_id=entry.get("hugging_face_id"),
        input_cost_per_token=float(pricing.get("input_cost_per_token") or 0.0),
        output_cost_per_token=float(pricing.get("output_cost_per_token") or 0.0),
        supports_system_messages=capabilities.get("supports_system_messages") is not False,
    )


class ModelApiClient:
    """Wywołania ``/chat/completions`` dla jednego modelu; zlicza tokeny i koszt."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str | None = None,
        timeout_seconds: float = 300.0,
        max_tokens: int = 8192,
        zero_data_retention: bool = True,
        model_info: ModelInfo | None = None,
        opener: Opener = urllib_request.urlopen,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._api_key = api_key
        self._timeout = timeout_seconds
        self._max_tokens = max_tokens
        self._zero_data_retention = zero_data_retention
        self._model_info = model_info
        self._opener = opener
        self.usage = Usage()

    def chat(self, messages: list[dict[str, Any]], *, json_mode: bool = False) -> str:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": 0,
            "max_tokens": self._max_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        if self._zero_data_retention:
            payload["routing"] = {"zero_data_retention": True}
        body = _request(
            f"{self.base_url}/chat/completions",
            payload=payload,
            api_key=self._api_key,
            timeout=self._timeout,
            opener=self._opener,
        )
        usage = body.get("usage") or {}
        self.usage.add(
            int(usage.get("prompt_tokens") or 0),
            int(usage.get("completion_tokens") or 0),
            self._model_info,
        )
        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ModelServiceUnavailableError("odpowiedź modelu bez treści") from exc
        if not isinstance(content, str):
            raise ModelServiceUnavailableError("odpowiedź modelu bez treści")
        return content

    def llm_transport(self, prompt: str, system_prompt: str, _attempt: int) -> str:
        """Transport dla ``LLMClient`` z #8 (parametr ``transport``) — wymusza tryb JSON."""
        if self._model_info is not None and not self._model_info.supports_system_messages:
            # Model bez roli "system" (np. Qwen3-VL w Eden AI): instrukcja w treści zapytania.
            messages = [{"role": "user", "content": f"{system_prompt}\n\n{prompt}"}]
        else:
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ]
        return self.chat(messages, json_mode=True)
