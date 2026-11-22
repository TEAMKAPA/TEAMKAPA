"""Narzędzia testów RAG: deterministyczny embedder (bez modelu) i baza wiedzy w pamięci."""

import hashlib
import math
import re
from collections.abc import Sequence
from typing import Any

from backend.worker.rag.knowledge import Candidate, Retrieval


class HashingEmbedder:
    """Wektor 384-wymiarowy z haszowania słów — identyczne teksty mają podobieństwo 1,0,
    teksty o wspólnym słownictwie — wysokie. Zastępuje e5 w testach (bez pobierania modelu)."""

    model_name = "test/hashing-embedder"
    dimension = 384

    def __init__(self) -> None:
        self.calls = 0

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls += 1
        vectors = []
        for text in texts:
            vector = [0.0] * self.dimension
            for token in re.findall(r"\w+", text.lower()):
                digest = hashlib.md5(token.encode(), usedforsecurity=False).digest()
                vector[int.from_bytes(digest[:4], "little") % self.dimension] += 1.0
            norm = math.sqrt(sum(x * x for x in vector)) or 1.0
            vectors.append([x / norm for x in vector])
        return vectors


class FakeKnowledgeBase:
    """Zwraca przygotowany wynik wyszukiwania i zapamiętuje zapytania."""

    def __init__(self, retrieval: Retrieval | Exception) -> None:
        self._retrieval = retrieval
        self.requests: list[dict[str, Any]] = []

    async def retrieve(self, *, job_id: str, sha256: str, signature: str, top_k: int) -> Retrieval:
        self.requests.append(
            {"job_id": job_id, "sha256": sha256, "signature": signature, "top_k": top_k}
        )
        if isinstance(self._retrieval, Exception):
            raise self._retrieval
        return self._retrieval


def candidate(
    doc_type: str,
    similarity: float,
    *,
    source: str = "catalog",
    form: dict[str, Any] | None = None,
    title: str | None = None,
) -> Candidate:
    return Candidate(doc_type, title or doc_type, similarity, source, form)  # type: ignore[arg-type]
