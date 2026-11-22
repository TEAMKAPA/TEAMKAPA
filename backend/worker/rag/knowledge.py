"""Baza wiedzy w PostgreSQL + pgvector: katalog typów, wyszukiwanie kNN, wektor zadania.

Wektory przekazujemy jako literał tekstowy ``'[x1,x2,…]'::vector`` — bez dodatkowej
biblioteki klienckiej (asyncpg zna tylko typy wbudowane).
"""

import asyncio
import json
import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from backend.worker.rag.catalog import catalog_form, catalog_types, signature_sha256
from backend.worker.rag.embedder import Embedder

logger = logging.getLogger(__name__)

Source = Literal["catalog", "approved", "hash"]


@dataclass(frozen=True, slots=True)
class Candidate:
    """Typ dokumentu z najlepszym przykładem tego typu."""

    doc_type: str
    title: str
    similarity: float
    source: Source
    form: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class Retrieval:
    candidates: list[Candidate]
    embedding_model: str
    exact: bool = False
    #: Wektor dokumentu (zapisany przy zadaniu; do pętli zwrotnej przy zatwierdzeniu).
    vector: list[float] = field(default_factory=list, repr=False)


class KnowledgeBase(Protocol):
    async def retrieve(
        self, *, job_id: str, sha256: str, signature: str, top_k: int
    ) -> Retrieval: ...


def vector_literal(vector: Sequence[float]) -> str:
    return "[" + ",".join(f"{x:.7g}" for x in vector) + "]"


def best_per_type(rows: Iterable[Candidate], top_k: int) -> list[Candidate]:
    """Najbliższy przykład każdego typu; przy remisie wygrywa przykład z formularzem COI."""
    best: dict[str, Candidate] = {}
    for row in rows:
        current = best.get(row.doc_type)
        if current is None or (row.similarity, row.form is not None) > (
            current.similarity,
            current.form is not None,
        ):
            best[row.doc_type] = row
    ranked = sorted(best.values(), key=lambda c: c.similarity, reverse=True)
    return ranked[:top_k]


class PgKnowledgeBase:
    def __init__(
        self,
        pool: Any,
        embedder: Embedder,
        *,
        exclude_types: frozenset[str] = frozenset(),
    ) -> None:
        self._pool = pool
        self._embedder = embedder
        #: Typy pomijane w wyszukiwaniu (ewaluacja leave-one-type-out).
        self._exclude = exclude_types

    @property
    def model(self) -> str:
        return self._embedder.model_name

    async def ensure_catalog(self) -> int:
        """Wpisuje katalog typów do bazy; przelicza wektory tylko zmienionych sygnatur."""
        types = catalog_types()
        existing = {
            row["doc_type"]: row["sha256"]
            for row in await self._pool.fetch(
                "SELECT doc_type, sha256 FROM knowledge_examples "
                "WHERE source = 'catalog' AND embedding_model = $1",
                self.model,
            )
        }
        changed = [
            t
            for t in types
            if t["signature"] and existing.get(t["doc_type"]) != signature_sha256(t["signature"])
        ]
        if not changed:
            return 0
        vectors = await asyncio.to_thread(self._embedder.embed, [t["signature"] for t in changed])
        async with self._pool.acquire() as connection, connection.transaction():
            for entry, vector in zip(changed, vectors, strict=True):
                await connection.execute(
                    "INSERT INTO document_types (code, title, source) VALUES ($1, $2, 'catalog') "
                    "ON CONFLICT (code) DO UPDATE SET title = EXCLUDED.title",
                    entry["doc_type"],
                    entry["title"],
                )
                form = catalog_form(entry)
                await connection.execute(
                    """
                    INSERT INTO knowledge_examples
                        (doc_type, source, sha256, embedding_model, embedding, form)
                    VALUES ($1, 'catalog', $2, $3, $4::vector, $5)
                    ON CONFLICT (doc_type, embedding_model) WHERE source = 'catalog'
                    DO UPDATE SET sha256 = EXCLUDED.sha256, embedding = EXCLUDED.embedding,
                                  form = EXCLUDED.form, created_at = now()
                    """,
                    entry["doc_type"],
                    signature_sha256(entry["signature"]),
                    self.model,
                    vector_literal(vector),
                    form,  # jsonb: kodek puli (backend.shared.db.pool) serializuje słownik
                )
        logger.info("Baza wiedzy: zaktualizowano %d typów katalogu", len(changed))
        return len(changed)

    async def _by_hash(self, sha256: str) -> Candidate | None:
        """Ten sam plik był już zatwierdzony — jego formularz jest najlepszym wzorcem."""
        row = await self._pool.fetchrow(
            """
            SELECT k.doc_type, t.title, k.form
            FROM knowledge_examples k
            JOIN approved_forms a ON a.id = k.approved_form_id
            JOIN document_types t ON t.code = k.doc_type
            WHERE a.sha256 = $1 AND NOT (k.doc_type = ANY($2::text[]))
            ORDER BY a.approved_at DESC
            LIMIT 1
            """,
            sha256,
            sorted(self._exclude),
        )
        if row is None:
            return None
        return Candidate(row["doc_type"], row["title"], 1.0, "hash", _form(row["form"]))

    async def retrieve(self, *, job_id: str, sha256: str, signature: str, top_k: int) -> Retrieval:
        (vector,) = await asyncio.to_thread(self._embedder.embed, [signature])
        literal = vector_literal(vector)
        # Wektor dokumentu przy zadaniu — przy zatwierdzeniu trafi do bazy wiedzy.
        await self._pool.execute(
            "UPDATE jobs SET embedding = $2::vector, embedding_model = $3 WHERE id = $1",
            job_id,
            literal,
            self.model,
        )
        exact = await self._by_hash(sha256)
        rows = await self._pool.fetch(
            """
            SELECT k.doc_type, t.title, k.source, k.form,
                   1 - (k.embedding <=> $1::vector) AS similarity
            FROM knowledge_examples k
            JOIN document_types t ON t.code = k.doc_type
            WHERE k.embedding_model = $2 AND NOT (k.doc_type = ANY($3::text[]))
            ORDER BY k.embedding <=> $1::vector
            LIMIT $4
            """,
            literal,
            self.model,
            sorted(self._exclude),
            top_k * 5,  # kilka przykładów na typ — agregacja do najlepszego
        )
        candidates = best_per_type(
            (
                Candidate(
                    r["doc_type"],
                    r["title"],
                    float(r["similarity"]),
                    r["source"],
                    _form(r["form"]),
                )
                for r in rows
            ),
            top_k,
        )
        if exact is not None:
            candidates = [exact, *[c for c in candidates if c.doc_type != exact.doc_type]]
        return Retrieval(candidates, self.model, exact=exact is not None, vector=vector)


def _form(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    data = json.loads(value) if isinstance(value, str) else value
    return data if isinstance(data, dict) else None
