"""Wektory dokumentów: multilingual-e5-small (otwarte wagi, MIT) uruchamiany lokalnie na CPU.

Model jest wbudowany w obraz workera (``HF_HUB_OFFLINE=1``) — worker nie ma internetu.
E5 wymaga prefiksu: dla porównań symetrycznych (dokument ↔ dokument) oba teksty dostają
``query: ``. Wektor = średnia stanów ukrytych po maskę uwagi, znormalizowana (L2), więc
iloczyn skalarny jest podobieństwem kosinusowym, a w pgvector używamy ``<=>`` (odległość
kosinusowa).

Do wektora trafia „sygnatura” dokumentu: tytuł i początek treści (nagłówek, adresat, pierwsze
etykiety pól) — to one odróżniają rodzaje wniosków; model i tak obcina wejście do 512 tokenów.
"""

import os
import re
import threading
from collections.abc import Sequence
from typing import Any, Protocol

#: Ile znaków treści trafia do sygnatury (≈ 512 tokenów e5 dla polskiego tekstu).
SIGNATURE_CHARS = 2000
_PREFIX = "query: "


class Embedder(Protocol):
    model_name: str
    dimension: int

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


def document_signature(document: dict[str, Any], max_chars: int = SIGNATURE_CHARS) -> str:
    """Tytuł + początek treści dokumentu, bez nadmiarowych odstępów i linii wypełnienia."""
    lines: list[str] = []
    if document.get("title"):
        lines.append(str(document["title"]))
    elements = document.get("elements") or []
    texts = [str(e.get("text", "")) for e in elements] or str(document.get("text", "")).splitlines()
    for text in texts:
        cleaned = " ".join(text.split())
        # Kropki i podkreślenia do wypełnienia ręcznego nie niosą treści.
        cleaned = re.sub(r"[.…_]{3,}", " ", cleaned).strip()
        # Wiersze arkuszy (ODS/XLSX) to komórki rozdzielone „|” — puste komórki to szum.
        cleaned = re.sub(r"(?:\s*\|\s*)+", " | ", cleaned).strip(" |")
        if len(re.sub(r"\W", "", cleaned)) >= 2:
            lines.append(cleaned)
    signature = "\n".join(lines)
    return signature[:max_chars]


class E5Embedder:
    """multilingual-e5 przez ``transformers`` (torch CPU jest już w obrazie przez Docling)."""

    dimension = 384

    def __init__(self, model_name: str, revision: str, *, max_length: int = 512) -> None:
        self.model_name = model_name
        self._revision = revision
        self._max_length = max_length
        self._lock = threading.Lock()
        self._model: Any = None
        self._tokenizer: Any = None

    def _load(self) -> tuple[Any, Any]:
        with self._lock:
            if self._model is None:
                from transformers import AutoModel, AutoTokenizer

                offline = os.environ.get("HF_HUB_OFFLINE") == "1"
                kwargs = {"revision": self._revision, "local_files_only": offline}
                self._tokenizer = AutoTokenizer.from_pretrained(self.model_name, **kwargs)
                model = AutoModel.from_pretrained(self.model_name, **kwargs)
                model.eval()
                self._model = model
            return self._model, self._tokenizer

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        import torch

        model, tokenizer = self._load()
        batch = tokenizer(
            [_PREFIX + text for text in texts],
            max_length=self._max_length,
            padding=True,
            truncation=True,
            return_tensors="pt",
        )
        with torch.inference_mode():
            hidden = model(**batch).last_hidden_state
            mask = batch["attention_mask"].unsqueeze(-1).to(hidden.dtype)
            pooled = (hidden * mask).sum(dim=1) / mask.sum(dim=1).clamp(min=1e-9)
            normalized = torch.nn.functional.normalize(pooled, p=2, dim=1)
        vectors: list[list[float]] = normalized.tolist()
        return vectors
