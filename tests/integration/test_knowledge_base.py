"""Baza wiedzy w prawdziwym PostgreSQL + pgvector: katalog, kNN, hash, pętla zwrotna.

Embedder deterministyczny (``HashingEmbedder``) — zamiast pobierać model e5 w CI; model
produkcyjny jest sprawdzany osobno (eval/rag_classification.py).
"""

from typing import Any

import pytest

from backend.shared.db.repository import PgJobStore
from backend.shared.jobs import DocumentRef
from backend.shared.schemas.coi_form import FormDefinition
from backend.shared.schemas.quality import AnalysisResult, QualityReport
from backend.worker.rag.catalog import catalog_types
from backend.worker.rag.knowledge import PgKnowledgeBase
from tests.coi_helpers import reference_forms, sample
from tests.integration.conftest import needs_postgres
from tests.rag_helpers import HashingEmbedder

pytestmark = [pytest.mark.anyio, needs_postgres]

SHA = "c" * 64


async def _job(store: PgJobStore, job_id: str, sha256: str = SHA) -> DocumentRef:
    ref = DocumentRef(
        job_id=job_id,
        filename="wniosek.pdf",
        extension="pdf",
        mime_type="application/pdf",
        size_bytes=10,
        sha256=sha256,
        owner="urzednik",
    )
    await store.create_job(ref)
    return ref


async def _succeed(store: PgJobStore, job_id: str, doc_type: str) -> FormDefinition:
    form = FormDefinition.model_validate(sample())
    report = QualityReport(document_sha256=SHA, document_type=doc_type, accuracy_mode="balanced")
    await store.mark_started(job_id)
    await store.mark_succeeded(job_id, AnalysisResult(form=form, report=report))
    return form


async def test_catalog_is_seeded_once_and_found_by_signature(db_pool: Any) -> None:
    embedder = HashingEmbedder()
    knowledge = PgKnowledgeBase(db_pool, embedder)
    assert await knowledge.ensure_catalog() == 9
    assert await knowledge.ensure_catalog() == 0  # sygnatury bez zmian — bez przeliczania
    assert embedder.calls == 1

    nadplata = next(t for t in catalog_types() if t["doc_type"] == "wniosek_nadplata_podatku")
    retrieval = await knowledge.retrieve(
        job_id="0" * 32, sha256=SHA, signature=nadplata["signature"], top_k=5
    )
    top = retrieval.candidates[0]
    assert (top.doc_type, top.source) == ("wniosek_nadplata_podatku", "catalog")
    assert top.similarity == pytest.approx(1.0, abs=1e-5)
    assert top.form == reference_forms()["wniosek_nadplata_podatku"]  # wzorzec COI z katalogu
    assert len(retrieval.candidates) == 5 and not retrieval.exact
    sims = [c.similarity for c in retrieval.candidates]
    assert sims == sorted(sims, reverse=True)


async def test_excluded_types_are_not_returned(db_pool: Any) -> None:
    knowledge = PgKnowledgeBase(
        db_pool, HashingEmbedder(), exclude_types=frozenset({"wniosek_nadplata_podatku"})
    )
    await knowledge.ensure_catalog()
    retrieval = await knowledge.retrieve(
        job_id="0" * 32, sha256=SHA, signature="nadpłata podatku", top_k=9
    )
    assert "wniosek_nadplata_podatku" not in {c.doc_type for c in retrieval.candidates}


async def test_approval_feeds_knowledge_base_and_creates_new_type(
    db_pool: Any, store: PgJobStore
) -> None:
    knowledge = PgKnowledgeBase(db_pool, HashingEmbedder())
    await knowledge.ensure_catalog()
    signature = "Wniosek o zezwolenie na sprzedaż alkoholu w sklepie spożywczym"

    # Analiza: wektor dokumentu zapisany przy zadaniu (typ nierozpoznany).
    await _job(store, "a" * 32)
    await knowledge.retrieve(job_id="a" * 32, sha256=SHA, signature=signature, top_k=5)
    form = await _succeed(store, "a" * 32, "unknown")
    stored = await db_pool.fetchval("SELECT embedding_model FROM jobs WHERE id = $1", "a" * 32)
    assert stored == "test/hashing-embedder"

    # Zatwierdzenie: nowy typ z tytułu formularza + przykład z wektorem dokumentu.
    await store.save_approval("a" * 32, "urzednik", SHA, form)
    code = await db_pool.fetchval("SELECT code FROM document_types WHERE source = 'approved'")
    assert code == "wniosek_o_ustalenie_numeru_porzadkowego_nieruchomosci"
    examples = await db_pool.fetch(
        "SELECT doc_type, source, form->>'title' AS title FROM knowledge_examples "
        "WHERE source = 'approved'"
    )
    assert [(e["doc_type"], e["title"]) for e in examples] == [(code, form.title)]

    # Ponowne zatwierdzenie aktualizuje przykład (bez duplikatu).
    await store.save_approval("a" * 32, "urzednik", SHA, form)
    assert (
        await db_pool.fetchval("SELECT count(*) FROM knowledge_examples WHERE source = 'approved'")
        == 1
    )

    # Ten sam plik ponownie: dopasowanie po skrócie, wzorcem jest zatwierdzony formularz.
    await _job(store, "b" * 32)
    retrieval = await knowledge.retrieve(job_id="b" * 32, sha256=SHA, signature="inna", top_k=5)
    assert retrieval.exact
    top = retrieval.candidates[0]
    assert (top.doc_type, top.source, top.similarity) == (code, "hash", 1.0)
    assert top.form is not None and top.form["title"] == form.title

    # Podobny dokument (inny plik): kNN znajduje typ z zatwierdzenia.
    await _job(store, "d" * 32, sha256="d" * 64)
    similar = await knowledge.retrieve(
        job_id="d" * 32, sha256="d" * 64, signature=signature, top_k=5
    )
    assert similar.candidates[0].doc_type == code
    assert similar.candidates[0].source == "approved"


async def test_approval_of_recognized_type_keeps_the_type(db_pool: Any, store: PgJobStore) -> None:
    knowledge = PgKnowledgeBase(db_pool, HashingEmbedder())
    await knowledge.ensure_catalog()
    await _job(store, "e" * 32)
    await knowledge.retrieve(job_id="e" * 32, sha256=SHA, signature="nadpłata", top_k=5)
    form = await _succeed(store, "e" * 32, "wniosek_nadplata_podatku")
    await store.save_approval("e" * 32, "urzednik", SHA, form)
    assert (
        await db_pool.fetchval("SELECT doc_type FROM knowledge_examples WHERE source = 'approved'")
        == "wniosek_nadplata_podatku"
    )
    assert (
        await db_pool.fetchval("SELECT count(*) FROM document_types WHERE source = 'approved'") == 0
    )


async def test_approval_without_vector_leaves_knowledge_base_unchanged(
    db_pool: Any, store: PgJobStore
) -> None:
    await _job(store, "f" * 32)
    form = await _succeed(store, "f" * 32, "unknown")
    await store.save_approval("f" * 32, "urzednik", SHA, form)  # analiza bez bazy wiedzy
    assert await store.get_approval("f" * 32, "urzednik") is not None
    assert await db_pool.fetchval("SELECT count(*) FROM knowledge_examples") == 0
