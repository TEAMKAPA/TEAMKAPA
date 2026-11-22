"""Baza wiedzy (RAG): katalog, sygnatura, decyzja o typie, wzorzec w zapytaniach, pewność."""

import dataclasses
import json
from pathlib import Path
from typing import Any

import pytest

from backend.shared.db.repository import document_type_code
from backend.shared.schemas.coi_form import FormDefinition
from backend.shared.schemas.quality import ConfidenceLevel
from backend.worker.clients.llm_client import LLMClient, LLMConfig
from backend.worker.pipeline.orchestrator import run_pipeline
from backend.worker.pipeline.prompts import (
    fields_prompt,
    structure_prompt,
    type_choice_prompt,
    type_choice_schema,
)
from backend.worker.pipeline.step05_doc_type import TEMPLATE_REGISTRY, run_step05
from backend.worker.pipeline.step06_structure import STRUCTURE_SCHEMA, run_step06
from backend.worker.pipeline.step07_fields import FIELDS_SCHEMA
from backend.worker.progress import ProgressReporter
from backend.worker.rag.catalog import catalog_form, catalog_types
from backend.worker.rag.embedder import SIGNATURE_CHARS, document_signature
from backend.worker.rag.knowledge import Retrieval, best_per_type, vector_literal
from backend.worker.rag.retrieval import (
    NEW_TYPE,
    TypeDecision,
    decide,
    example_listing,
    llm_chooser,
    subject_tabs,
)
from tests.coi_helpers import reference_forms
from tests.pipeline_helpers import (
    RecordingStore,
    incoming,
    representative_document,
    settings,
    stages,
)
from tests.rag_helpers import FakeKnowledgeBase, HashingEmbedder, candidate

NADPLATA = "wniosek_nadplata_podatku"


# ------------------------------------------------------------------ katalog i sygnatura


def test_catalog_has_our_nine_types_with_signatures_and_valid_forms() -> None:
    types = catalog_types()
    assert len(types) == 9
    assert all(t["signature"] and t["title"] and t["keywords"] for t in types)
    assert len({t["doc_type"] for t in types}) == 9
    forms = {t["doc_type"]: catalog_form(t) for t in types}
    with_form = {code for code, form in forms.items() if form}
    assert with_form == {"wniosek_numer_porzadkowy", NADPLATA}
    for code in with_form:
        FormDefinition.model_validate(forms[code])
    assert types == TEMPLATE_REGISTRY


def test_document_signature_skips_fill_lines_and_empty_cells() -> None:
    document = {
        "title": "Wniosek o zwrot opłaty skarbowej",
        "elements": [
            {"text": "Imię   i\nnazwisko ……………………"},
            {"text": "........................"},
            {"text": "Kwota |  |  | zł |  | "},
            {"text": "x"},
        ],
    }
    assert document_signature(document).splitlines() == [
        "Wniosek o zwrot opłaty skarbowej",
        "Imię i nazwisko",
        "Kwota | zł",
    ]
    long = {"text": "słowo " * 2000}
    assert len(document_signature(long)) == SIGNATURE_CHARS


def test_hashing_embedder_ranks_same_type_highest() -> None:
    embedder = HashingEmbedder()
    texts = [t["signature"] for t in catalog_types()]
    vectors = embedder.embed(texts)
    query = embedder.embed([texts[4]])[0]
    sims = [sum(a * b for a, b in zip(query, v, strict=True)) for v in vectors]
    assert max(range(9), key=sims.__getitem__) == 4
    assert vector_literal([0.5, -0.25]) == "[0.5,-0.25]"


# ------------------------------------------------------------------ decyzja o typie


def _retrieval(*candidates: Any, exact: bool = False) -> Retrieval:
    return Retrieval(list(candidates), "test/hashing-embedder", exact=exact)


def test_best_per_type_keeps_nearest_example_preferring_one_with_form() -> None:
    form = {"title": "x"}
    rows = [
        candidate("a", 0.91),
        candidate("a", 0.95, source="approved"),
        candidate("b", 0.93),
        candidate("b", 0.93, form=form),
    ]
    best = best_per_type(rows, top_k=5)
    assert [(c.doc_type, c.similarity, c.source) for c in best] == [
        ("a", 0.95, "approved"),
        ("b", 0.93, "catalog"),
    ]
    assert best[1].form == form


@pytest.mark.parametrize(
    ("similarity", "choice", "method", "doc_type"),
    [
        (0.97, None, "knn", NADPLATA),  # ≥ progu akceptacji — bez pytania modelu
        (0.85, NADPLATA, "new", None),  # < progu odrzucenia — nowy typ, model nie pytany
        (0.92, NADPLATA, "llm", NADPLATA),  # strefa niepewności — wybiera model
        (0.92, None, "llm", None),  # model: „nowy typ”
    ],
)
def test_decision_cascade(
    similarity: float, choice: str | None, method: str, doc_type: str | None
) -> None:
    asked: list[list[str]] = []

    def chooser(signature: str, candidates: list[Any]) -> str | None:
        asked.append([c.doc_type for c in candidates])
        return choice

    retrieval = _retrieval(candidate(NADPLATA, similarity), candidate("inny", 0.80))
    decision = decide(retrieval, signature="s", accept=0.95, reject=0.90, chooser=chooser)
    assert (decision.method, decision.doc_type) == (method, doc_type)
    assert bool(asked) == (method == "llm")


def test_exact_hash_wins_and_brings_approved_example() -> None:
    form = reference_forms()[NADPLATA]
    retrieval = _retrieval(candidate(NADPLATA, 1.0, source="hash", form=form), exact=True)
    decision = decide(retrieval, signature="s", accept=0.95, reject=0.90, chooser=None)
    assert (decision.method, decision.example_source) == ("hash", "approved")
    assert decision.example == form
    report = decision.report()
    assert report.candidates[0].source == "hash" and report.similarity == 1.0


def test_gray_zone_without_model_is_new_type_and_empty_base_too() -> None:
    gray = _retrieval(candidate(NADPLATA, 0.92))
    assert decide(gray, signature="s", accept=0.95, reject=0.9, chooser=None).doc_type is None
    empty = decide(_retrieval(), signature="s", accept=0.95, reject=0.9, chooser=None)
    assert (empty.method, empty.doc_type, empty.similarity) == ("new", None, None)


def test_llm_chooser_offers_candidates_and_new_type() -> None:
    seen: dict[str, str] = {}

    def transport(prompt: str, system: str, attempt: int) -> str:
        seen["prompt"] = prompt
        return json.dumps({"doc_type": NADPLATA})

    client = LLMClient(LLMConfig(model="m", api_key="k", offline=False), transport=transport)
    choose = llm_chooser(client)
    candidates = [candidate(NADPLATA, 0.93, title="Wniosek o nadpłatę"), candidate("x", 0.91)]
    assert choose("Początek wniosku", candidates) == NADPLATA
    assert "Wniosek o nadpłatę" in seen["prompt"] and NEW_TYPE in seen["prompt"]

    demo = LLMClient(LLMConfig(model="m", api_key="", offline=True))
    assert llm_chooser(demo)("Początek", candidates) is None  # tryb demo: zawsze nowy typ


def test_type_choice_schema_limits_answers() -> None:
    schema = type_choice_schema(["a", "b"], NEW_TYPE)
    assert schema["properties"]["doc_type"]["enum"] == ["a", "b", NEW_TYPE]
    prompt = type_choice_prompt(
        "Treść", [("a", "Wniosek A", 0.931, "Urząd\n  Wniosek A o coś")], NEW_TYPE
    )
    assert "- a: „Wniosek A” (podobieństwo 0.931)" in prompt
    assert "Przykład tego rodzaju: Urząd Wniosek A o coś" in prompt


def test_candidates_are_described_by_catalog_document_or_example_fields() -> None:
    from backend.worker.rag.retrieval import describe

    catalog = describe(candidate(NADPLATA, 0.92))
    assert "o stwierdzenie nadpłaty podatku" in catalog  # początek dokumentu z katalogu
    approved = describe(candidate("nowy", 0.92, form=reference_forms()[NADPLATA]))
    assert "rozliczenie_nadplaty" in approved
    assert describe(candidate("nowy", 0.92, title="Wniosek X")) == "Wniosek X"


# ------------------------------------------------------------------ etap 05 z decyzją RAG


def test_step05_uses_rag_decision_and_attaches_example() -> None:
    form = reference_forms()[NADPLATA]
    decision = TypeDecision(NADPLATA, "Nadpłata", "knn", 0.97, example=form)
    result = run_step05({"text": "Cokolwiek"}, decision=decision)
    assert (result["doc_type"], result["method"]) == (NADPLATA, "knn")
    template = result["selected_template"]
    assert template["title"] == "Wniosek o stwierdzenie nadpłaty podatku"
    assert template["example_form"] == form


def test_step05_type_created_from_approval_gets_synthetic_template() -> None:
    decision = TypeDecision("wniosek_o_cos_nowego", "Wniosek o coś nowego", "knn", 0.99)
    result = run_step05({"text": "x"}, decision=decision)
    assert result["selected_template"]["title"] == "Wniosek o coś nowego"
    assert result["selected_template"]["example_form"] is None


def test_step05_new_type_has_no_template_even_with_lexical_match() -> None:
    decision = TypeDecision(None, None, "new", 0.87)
    text = "WNIOSEK O STWIERDZENIE NADPŁATY PODATKU"
    result = run_step05({"text": text}, decision=decision)
    assert (result["doc_type"], result["selected_template"]) == ("unknown", None)


# ------------------------------------------------------------------ wzorzec w zapytaniach


def test_subject_tabs_skip_standard_pages() -> None:
    form = reference_forms()["wniosek_numer_porzadkowy"]
    assert [t["title"] for t in subject_tabs(form)] == ["Informacje o budynku"]
    listing = example_listing(reference_forms()[NADPLATA])
    assert "Strona „Informacje o nadpłacie”" in listing
    assert "- kwota (NUMBER) „Kwota”" in listing
    assert "opcje: ['Zaliczyć na poczet przyszłego zobowiązania podatkowego'" in listing
    assert "numer_rachunku (TEXT_FIELD) „Numer rachunku” [warunkowe]" in listing
    assert "twoje_dane" not in listing and "zgoda" not in listing


def test_prompts_include_example_only_when_present() -> None:
    document = representative_document().as_step_payload()
    example = reference_forms()[NADPLATA]
    with_example = structure_prompt(document, "Nadpłata", STRUCTURE_SCHEMA, example=example)
    assert "Wzorzec z bazy wiedzy" in with_example and "rozliczenie_nadplaty" in with_example
    assert "Wzorzec z bazy wiedzy" not in structure_prompt(document, None, STRUCTURE_SCHEMA)
    structure = run_step06(document)
    fields = fields_prompt(document, structure, None, FIELDS_SCHEMA, example=example)
    assert "korekta_deklaracji" in fields


# ------------------------------------------------------------------ orkiestrator


async def _run(tmp_path: Path, knowledge: Any, **overrides: Any) -> Any:
    progress = ProgressReporter(RecordingStore(), "b" * 32, 60)
    with_knowledge = dataclasses.replace(stages(), knowledge=knowledge)
    return await run_pipeline(incoming(tmp_path), progress, settings(**overrides), with_knowledge)


@pytest.mark.anyio
async def test_pipeline_uses_knowledge_base_and_reports_retrieval(tmp_path: Path) -> None:
    example = reference_forms()[NADPLATA]
    knowledge = FakeKnowledgeBase(
        _retrieval(candidate(NADPLATA, 0.97, form=example), candidate("inny", 0.90))
    )
    result = await _run(tmp_path, knowledge)
    report = result.report
    assert report.document_type == NADPLATA
    assert report.retrieval.method == "knn"
    assert report.retrieval.example_source == "catalog"
    assert [c.doc_type for c in report.retrieval.candidates] == [NADPLATA, "inny"]
    (request,) = knowledge.requests
    assert request["sha256"] == incoming(tmp_path).ref.sha256
    assert request["signature"].startswith("Wniosek o wydanie dowodu osobistego")
    # Pola od modelu mają sygnał zgodności ze wzorcem; pola szablonu COI — nie.
    generated = [a for a in report.fields if "source_found" in a.signals]
    assert generated and all("example_match" in a.signals for a in generated)
    assert all(
        "example_match" not in a.signals for a in report.fields if "standard_template" in a.signals
    )


@pytest.mark.anyio
async def test_pipeline_without_knowledge_base_degrades_to_lexical(tmp_path: Path) -> None:
    result = await _run(tmp_path, FakeKnowledgeBase(ConnectionError("baza niedostępna")))
    report = result.report
    assert report.retrieval.method == "lexical"
    assert "KNOWLEDGE_BASE_UNAVAILABLE" in {i.code for i in report.issues}
    assert all(a.level is not ConfidenceLevel.HIGH for a in report.fields)


@pytest.mark.anyio
async def test_rag_disabled_skips_knowledge_base(tmp_path: Path) -> None:
    knowledge = FakeKnowledgeBase(_retrieval(candidate(NADPLATA, 0.99)))
    result = await _run(tmp_path, knowledge, rag_enabled=False)
    assert knowledge.requests == [] and result.report.retrieval.method == "lexical"


# ------------------------------------------------------------------ pętla zwrotna


@pytest.mark.parametrize(
    ("title", "code"),
    [
        ("Wniosek o zezwolenie na sprzedaż alkoholu", "wniosek_o_zezwolenie_na_sprzedaz_alkoholu"),
        ("Zgłoszenie — łódź!", "zgloszenie_lodz"),
        ("???", "dokument"),
    ],
)  # fmt: skip
def test_document_type_code_from_title(title: str, code: str) -> None:
    assert document_type_code(title) == code
    assert len(document_type_code("a" * 300)) == 100
