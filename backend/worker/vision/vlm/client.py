"""Klient VLM za interfejsem (#11): Eden AI (domyślny), lokalny (awaryjny) i fake (testy).

Oba prawdziwe backendy mówią API zgodnym z OpenAI (``POST {base}/chat/completions``):

- jedna wiadomość ``user`` z tekstem i obrazem (``image_url`` z adresem ``data:``); Qwen3-VL
  w Eden AI nie obsługuje komunikatów systemowych, więc instrukcje są w tej samej wiadomości,
- **bez narzędzi** (``tools`` nie są wysyłane), ``temperature`` 0, ``seed`` tylko gdy dostawca go
  obsługuje (``VLM_SEND_SEED``), ``response_format`` = schemat JSON (``json_schema``, strict) albo
  ``json_object``; odpowiedź i tak przechodzi walidację Pydantic w wywołującym,
- przekierowania nie są wykonywane (jedynym celem połączeń jest skonfigurowany adres),
- 429, 5xx i błędy sieci: ``transport_retries`` ponowień, potem ``VlmUnavailable``; inne 4xx bez
  ponowień. Komunikaty błędów zawierają tylko kod HTTP albo typ wyjątku, nigdy treść odpowiedzi,
  nagłówki ani token.
"""

import base64
import hashlib
import json
import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from backend.worker.vision.vlm.config import ResponseFormat, VlmBackend, VlmSettings

logger = logging.getLogger(__name__)

VLM_UNAVAILABLE = "VLM_UNAVAILABLE"
VLM_INVALID_RESPONSE = "VLM_INVALID_RESPONSE"


class VlmError(Exception):
    """Błąd VLM z kodem powodu; ``detail`` nie zawiera treści dokumentu ani sekretów."""

    code = VLM_UNAVAILABLE

    def __init__(self, detail: str) -> None:
        super().__init__(f"{self.code}: {detail}")
        self.detail = detail


class VlmUnavailable(VlmError):
    code = VLM_UNAVAILABLE


class VlmInvalidResponse(VlmError):
    code = VLM_INVALID_RESPONSE


@dataclass(frozen=True)
class VlmImage:
    data: bytes
    mime: str = "image/png"

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.data).hexdigest()

    def data_url(self) -> str:
        return f"data:{self.mime};base64,{base64.b64encode(self.data).decode('ascii')}"


@dataclass(frozen=True)
class VlmRequest:
    task: str  # np. "roles", "fields"
    prompt: str  # pełny tekst wiadomości (instrukcje + blok danych)
    prompt_version: str  # nazwa pliku promptu i skrót jego treści
    image: VlmImage
    schema_name: str
    schema: Mapping[str, Any]


@dataclass(frozen=True)
class VlmUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass(frozen=True)
class VlmResponse:
    content: str
    model: str
    usage: VlmUsage = field(default_factory=VlmUsage)
    latency_s: float = 0.0
    cached: bool = False


class VlmClient(Protocol):
    name: str
    model: str

    def complete(self, request: VlmRequest) -> VlmResponse: ...


def generation_params(settings: VlmSettings) -> dict[str, Any]:
    """Parametry generowania wchodzące do klucza cache i do żądania (bez schematu)."""
    params: dict[str, Any] = {
        "temperature": settings.temperature,
        "max_tokens": settings.max_tokens,
        "response_format": settings.response_format.value,
        "image_max_side_px": settings.image_max_side_px,
    }
    if settings.send_seed:
        params["seed"] = settings.seed
    return params


def build_payload(request: VlmRequest, settings: VlmSettings) -> dict[str, Any]:
    params = generation_params(settings)
    payload: dict[str, Any] = {
        "model": settings.model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": request.prompt},
                    {"type": "image_url", "image_url": {"url": request.image.data_url()}},
                ],
            }
        ],
        "temperature": params["temperature"],
        "max_tokens": params["max_tokens"],
        "stream": False,
    }
    if "seed" in params:
        payload["seed"] = params["seed"]
    if settings.response_format is ResponseFormat.JSON_SCHEMA:
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": request.schema_name,
                "strict": True,
                "schema": dict(request.schema),
            },
        }
    else:
        payload["response_format"] = {"type": "json_object"}
    return payload


def _endpoint(base_url: str) -> str:
    base = base_url.rstrip("/")
    return base if base.endswith("/chat/completions") else f"{base}/chat/completions"


class OpenAICompatibleClient:
    """Eden AI albo lokalny serwer zgodny z OpenAI (vLLM, llama.cpp)."""

    def __init__(
        self,
        settings: VlmSettings,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if settings.backend is VlmBackend.FAKE:
            raise ValueError("backend fake nie łączy się z siecią: użyj FakeVlmClient")
        self._settings = settings
        self._transport = transport
        self._sleep = sleep
        self.name = settings.backend.value
        self.model = settings.model

    def __repr__(self) -> str:
        return f"OpenAICompatibleClient(backend={self.name}, model={self.model})"

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._settings.backend is VlmBackend.EDENAI:
            key = self._settings.edenai_api_key
            if key is None or not key.get_secret_value().strip():
                raise VlmUnavailable("brak tokenu Eden AI (EDENAI_API_KEY)")
            headers["Authorization"] = f"Bearer {key.get_secret_value()}"
        return headers

    def complete(self, request: VlmRequest) -> VlmResponse:
        payload = build_payload(request, self._settings)
        url = _endpoint(self._settings.base_url)
        attempts = self._settings.transport_retries + 1
        last = "brak odpowiedzi"
        for attempt in range(attempts):
            started = time.monotonic()
            try:
                with httpx.Client(
                    timeout=self._settings.timeout_s,
                    follow_redirects=False,
                    transport=self._transport,
                ) as client:
                    response = client.post(url, json=payload, headers=self._headers())
            except httpx.TimeoutException:
                last = "przekroczony limit czasu"
            except httpx.HTTPError as exc:
                last = f"błąd połączenia ({type(exc).__name__})"
            else:
                status = response.status_code
                if 200 <= status < 300:
                    return self._parse(response, time.monotonic() - started)
                last = f"HTTP {status}"
                if not (status == 429 or status >= 500):
                    break  # 3xx (przekierowanie odrzucone) i inne 4xx: bez ponowień
            logger.warning(
                "VLM %s: próba %d/%d nieudana (%s)", request.task, attempt + 1, attempts, last
            )
            if attempt + 1 < attempts:
                self._sleep(self._settings.retry_backoff_s * (2**attempt))
        raise VlmUnavailable(last)

    def _parse(self, response: httpx.Response, latency: float) -> VlmResponse:
        try:
            body = response.json()
            content = body["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise VlmInvalidResponse(
                f"niepoprawna koperta odpowiedzi ({type(exc).__name__})"
            ) from None
        if not isinstance(content, str) or not content.strip():
            raise VlmInvalidResponse("pusta treść odpowiedzi")
        usage = body.get("usage") or {}
        return VlmResponse(
            content=content,
            model=str(body.get("model") or self.model),
            usage=VlmUsage(
                prompt_tokens=int(usage.get("prompt_tokens") or 0),
                completion_tokens=int(usage.get("completion_tokens") or 0),
            ),
            latency_s=round(latency, 3),
        )


Responder = Callable[[VlmRequest], str]


class FakeVlmClient:
    """Klient testowy bez sieci: odpowiedzi z funkcji albo słownika ``task`` → treść JSON.

    ``failures`` (kolejka błędów) pozwala symulować awarie i niepoprawne odpowiedzi.
    """

    name = "fake"

    def __init__(
        self,
        responses: Responder | Mapping[str, str | list[str]],
        *,
        model: str = "fake/vlm",
        failures: list[VlmError] | None = None,
    ) -> None:
        self.model = model
        self._responses = responses
        self._failures = list(failures or [])
        self._counters: dict[str, int] = {}
        self.requests: list[VlmRequest] = []

    def complete(self, request: VlmRequest) -> VlmResponse:
        self.requests.append(request)
        if self._failures:
            raise self._failures.pop(0)
        if callable(self._responses):
            content = self._responses(request)
        else:
            value = self._responses[request.task]
            if isinstance(value, list):
                index = self._counters.get(request.task, 0)
                self._counters[request.task] = index + 1
                content = value[min(index, len(value) - 1)]
            else:
                content = value
        tokens = len(request.prompt) // 4 + 1000  # przybliżenie (tekst + obraz)
        return VlmResponse(
            content=content,
            model=self.model,
            usage=VlmUsage(prompt_tokens=tokens, completion_tokens=len(content) // 4),
        )


def build_client(
    settings: VlmSettings, *, transport: httpx.BaseTransport | None = None
) -> VlmClient:
    """Klient z konfiguracji; backend ``fake`` wymaga nagrań, więc tworzy się go bezpośrednio."""
    if settings.backend is VlmBackend.FAKE:
        raise ValueError("VLM_BACKEND=fake: utwórz FakeVlmClient z nagranymi odpowiedziami")
    return OpenAICompatibleClient(settings, transport=transport)


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
