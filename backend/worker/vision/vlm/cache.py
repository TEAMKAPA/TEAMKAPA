"""Cache odpowiedzi VLM i liczniki wywołań (#11, D.3).

Klucz to SHA-256 z: zadania, hasha obrazu strony, modelu, wersji promptu, hasha pełnej treści
promptu (dane strony), nazwy i hasha schematu odpowiedzi oraz parametrów generowania. Ten sam obraz
z tym samym promptem nie jest wysyłany ponownie, więc ponowna ewaluacja nie zużywa tokenów.

Cache leży wyłącznie w ``dane/robocze/vlm_cache/`` (``VLM_CACHE_DIR``), poza repozytorium:
pliki zawierają odpowiedzi modelu, a więc treści formularzy. Do logów trafiają tylko zadanie, skrót
klucza, model, liczby tokenów, czas i kody błędów.
"""

import hashlib
import json
import logging
import os
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.worker.vision.vlm.client import (
    VlmClient,
    VlmError,
    VlmRequest,
    VlmResponse,
    VlmUsage,
    canonical_json,
)

logger = logging.getLogger(__name__)

CACHE_SCHEMA = "teamkapa.vlm.cache/1"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def cache_key(request: VlmRequest, model: str, params: dict[str, Any]) -> str:
    material = {
        "task": request.task,
        "image_sha256": request.image.sha256,
        "image_mime": request.image.mime,
        "model": model,
        "prompt_version": request.prompt_version,
        "prompt_sha256": _sha(request.prompt),
        "schema_name": request.schema_name,
        "schema_sha256": _sha(canonical_json(request.schema)),
        "params": params,
    }
    return _sha(canonical_json(material))


@dataclass
class VlmStats:
    """Liczniki bez treści: wywołania, trafienia cache, tokeny, czas i błędy według kodu."""

    calls: int = 0
    cache_hits: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_s: float = 0.0
    errors: Counter[str] = field(default_factory=Counter)

    def as_dict(self) -> dict[str, Any]:
        return {
            "calls": self.calls,
            "cache_hits": self.cache_hits,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.prompt_tokens + self.completion_tokens,
            "latency_s": round(self.latency_s, 3),
            "errors": dict(sorted(self.errors.items())),
        }


class CachedVlmClient:
    """Opakowanie klienta: odpowiedź z cache albo wywołanie i zapis (atomowy) do cache."""

    def __init__(
        self,
        inner: VlmClient,
        params: dict[str, Any],
        cache_dir: Path | None,
        *,
        stats: VlmStats | None = None,
    ) -> None:
        self._inner = inner
        self._params = params
        self._dir = cache_dir
        self.stats = stats or VlmStats()
        self.name = f"{inner.name}+cache" if cache_dir else inner.name
        self.model = inner.model

    def _path(self, key: str) -> Path | None:
        return self._dir / key[:2] / f"{key}.json" if self._dir else None

    def complete(self, request: VlmRequest) -> VlmResponse:
        key = cache_key(request, self.model, self._params)
        path = self._path(key)
        if path is not None and path.is_file():
            record = json.loads(path.read_text(encoding="utf-8"))
            usage = VlmUsage(**record["usage"])
            self.stats.cache_hits += 1
            logger.info("VLM %s: cache %s (model %s)", request.task, key[:12], self.model)
            return VlmResponse(
                content=record["content"], model=record["model"], usage=usage, cached=True
            )
        try:
            response = self._inner.complete(request)
        except VlmError as exc:
            self.stats.errors[exc.code] += 1
            logger.warning("VLM %s: %s (klucz %s)", request.task, exc.code, key[:12])
            raise
        self.stats.calls += 1
        self.stats.prompt_tokens += response.usage.prompt_tokens
        self.stats.completion_tokens += response.usage.completion_tokens
        self.stats.latency_s += response.latency_s
        logger.info(
            "VLM %s: wywołanie %s, model %s, tokeny %d+%d, %.2f s",
            request.task,
            key[:12],
            response.model,
            response.usage.prompt_tokens,
            response.usage.completion_tokens,
            response.latency_s,
        )
        if path is not None:
            self._store(path, key, request, response)
        return response

    def _store(self, path: Path, key: str, request: VlmRequest, response: VlmResponse) -> None:
        record = {
            "schema_version": CACHE_SCHEMA,
            "key": key,
            "task": request.task,
            "prompt_version": request.prompt_version,
            "image_sha256": request.image.sha256,
            "model": response.model,
            "usage": {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
            },
            "created": datetime.now(UTC).isoformat(timespec="seconds"),
            "content": response.content,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, path)
