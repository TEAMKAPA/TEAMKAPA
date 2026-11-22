"""Decyzja o typie dokumentu (kaskada z planu) i wzorzec formularza dla zapytań do modelu.

Kaskada:
1. ``hash``  — ten sam plik był już zatwierdzony: jego formularz jest wzorcem,
2. ``knn``   — najbliższy typ z podobieństwem ≥ ``rag_accept_similarity``,
3. ``llm``   — podobieństwo w strefie niepewności: model wybiera spośród top-k albo „nowy typ”,
4. ``new``   — podobieństwo < ``rag_reject_similarity``: dokument spoza bazy wiedzy.

Progi z kalibracji (``eval/rag_classification.py``, 9 formularzy × 5 formatów): podobieństwo
do własnego typu ≥ 0,95, do obcego typu ≤ 0,93 (e5 daje wysokie kosinusy dla wszystkich
polskich pism urzędowych, stąd wąskie pasmo).
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from backend.shared.schemas.quality import RetrievalReport, TypeCandidate
from backend.worker.pipeline.prompts import SYSTEM_PROMPT, type_choice_prompt, type_choice_schema
from backend.worker.pipeline.standard_sections import is_standard_section
from backend.worker.rag.catalog import catalog_types
from backend.worker.rag.knowledge import Candidate, Retrieval

if TYPE_CHECKING:
    from backend.worker.clients.llm_client import LLMClient

Method = Literal["hash", "knn", "llm", "new", "lexical"]
#: Wybór modelu: (sygnatura dokumentu, kandydaci) → doc_type albo None (nowy typ).
Chooser = Callable[[str, list[Candidate]], str | None]

NEW_TYPE = "NOWY"


@dataclass(frozen=True, slots=True)
class TypeDecision:
    doc_type: str | None
    title: str | None
    method: Method
    similarity: float | None = None
    candidates: list[Candidate] = field(default_factory=list)
    example: dict[str, Any] | None = None
    example_source: Literal["catalog", "approved"] | None = None
    embedding_model: str | None = None

    def report(self) -> RetrievalReport:
        return RetrievalReport(
            method=self.method,
            similarity=round(self.similarity, 4) if self.similarity is not None else None,
            embedding_model=self.embedding_model,
            candidates=[
                TypeCandidate(
                    doc_type=c.doc_type,
                    title=c.title,
                    similarity=round(c.similarity, 4),
                    source=c.source,
                )
                for c in self.candidates
            ],
            example_source=self.example_source,
        )


def _chosen(candidate: Candidate, method: Method, retrieval: Retrieval) -> TypeDecision:
    source: Literal["catalog", "approved"] | None = None
    if candidate.form is not None:
        source = "catalog" if candidate.source == "catalog" else "approved"
    return TypeDecision(
        doc_type=candidate.doc_type,
        title=candidate.title,
        method=method,
        similarity=candidate.similarity,
        candidates=retrieval.candidates,
        example=candidate.form,
        example_source=source,
        embedding_model=retrieval.embedding_model,
    )


def decide(
    retrieval: Retrieval,
    *,
    signature: str,
    accept: float,
    reject: float,
    chooser: Chooser | None,
) -> TypeDecision:
    def new(method: Method) -> TypeDecision:
        top = retrieval.candidates[0].similarity if retrieval.candidates else None
        return TypeDecision(
            None,
            None,
            method,
            similarity=top,
            candidates=retrieval.candidates,
            embedding_model=retrieval.embedding_model,
        )

    if not retrieval.candidates:
        return new("new")
    top = retrieval.candidates[0]
    if retrieval.exact:
        return _chosen(top, "hash", retrieval)
    if top.similarity >= accept:
        return _chosen(top, "knn", retrieval)
    if top.similarity < reject or chooser is None:
        return new("new")
    # Strefa niepewności — wybiera model językowy (albo wskazuje nowy typ).
    choice = chooser(signature, retrieval.candidates)
    for candidate in retrieval.candidates:
        if candidate.doc_type == choice:
            return _chosen(candidate, "llm", retrieval)
    return new("llm")


def describe(candidate: Candidate) -> str:
    """Treść, z którą model porównuje dokument: początek dokumentu wzorcowego z katalogu,
    a dla typów z zatwierdzeń (bez zapisanej treści dokumentu) — pola wzorca formularza."""
    signatures = {t["doc_type"]: t["signature"] for t in catalog_types()}
    if candidate.doc_type in signatures:
        return str(signatures[candidate.doc_type])
    if candidate.form:
        return example_listing(candidate.form)
    return candidate.title


def llm_chooser(client: LLMClient) -> Chooser:
    """Model wybiera typ spośród kandydatów; w trybie demo (bez modelu) — zawsze nowy typ."""

    def choose(signature: str, candidates: list[Candidate]) -> str | None:
        codes = [c.doc_type for c in candidates]
        described = [(c.doc_type, c.title, c.similarity, describe(c)) for c in candidates]
        output = client.generate_structured(
            type_choice_prompt(signature, described, NEW_TYPE),
            type_choice_schema(codes, NEW_TYPE),
            system_prompt=SYSTEM_PROMPT,
            mock_response_fn=lambda _model: {"doc_type": NEW_TYPE},
        )
        choice = str(output.get("doc_type") or NEW_TYPE)
        return None if choice == NEW_TYPE else choice

    return choose


# ------------------------------------------------------------------ wzorzec dla zapytań

_GRID = "repeatingSectionGrid"


def subject_tabs(form: dict[str, Any]) -> list[dict[str, Any]]:
    """Strony tematyczne wzorca — bez stron standardowych COI i strony końcowej."""
    tabs = []
    for tab in form.get("tabs", []):
        if tab.get("type") != "MAIN":
            continue
        names = [str(q.get("field", {}).get("techName", "")) for q in tab.get("questions", [])]
        if any(not is_standard_section(name) for name in names):
            tabs.append(tab)
    return tabs


def example_listing(form: dict[str, Any]) -> str:
    """Zwięzły opis stron tematycznych wzorca: sekcje, pola, typy, opcje, warunki."""
    lines: list[str] = []
    for tab in subject_tabs(form):
        lines.append(f"Strona „{tab.get('title')}”:")
        for question in tab.get("questions", []):
            section = question.get("field", {})
            conditional = " [warunkowa]" if section.get("states") else ""
            lines.append(
                f"  sekcja {section.get('techName')} „{section.get('title')}”{conditional}"
            )
            for child in section.get(_GRID, []):
                f = child.get("field", {})
                extra = ""
                options = [o.get("name") for o in f.get("options") or []]
                if options:
                    extra += f" opcje: {options}"
                if f.get("dictionaryName"):
                    extra += f" słownik: {f['dictionaryName']}"
                if f.get("states"):
                    extra += " [warunkowe]"
                lines.append(
                    f"    - {f.get('techName')} ({f.get('type')}) „{f.get('title')}”{extra}"
                )
    return "\n".join(lines)
