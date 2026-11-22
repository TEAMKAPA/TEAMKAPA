"""Step 05: Document type recognition (top-5) and local COI template selection."""

from __future__ import annotations

import re
import unicodedata
from copy import deepcopy
from difflib import SequenceMatcher
from typing import Any

from backend.worker.rag.catalog import catalog_types
from backend.worker.rag.retrieval import TypeDecision

#: Typy z katalogu RAG — nasze formularze od organizatora. Dopasowanie leksykalne działa,
#: gdy baza wiedzy (kNN) jest niedostępna albo wyłączona (``RAG_ENABLED=false``).
TEMPLATE_REGISTRY: list[dict[str, Any]] = catalog_types()


def _normalize(text: str) -> str:
    replaced = text.lower().replace("ł", "l")
    decomposed = unicodedata.normalize("NFKD", replaced)
    cleaned = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", cleaned).strip()


def _tokenize(text: str) -> set[str]:
    return {tok for tok in re.findall(r"[a-z0-9]+", _normalize(text)) if len(tok) >= 2}


_TITLE_LINES = 20
_TITLE_LINE_MATCH = 0.8


def _score_template(text: str, template: dict[str, Any]) -> tuple[float, list[str]]:
    norm_text = _normalize(text)
    tokens = _tokenize(text)
    signals: list[str] = []

    # 1. Form code exact match (strongest deterministic signal).
    code = template["code"]
    code_score = 0.0
    # Pusty kod (nasze formularze go nie mają) pasowałby do każdego tekstu.
    if code and re.search(rf"\b{re.escape(_normalize(code))}\b", norm_text):
        code_score = 1.0
        signals.append(f"code:{code}")

    # 2. Keyword overlap.
    keywords = template["keywords"]
    matched_kw = [kw for kw in keywords if _normalize(kw) in norm_text]
    kw_score = len(matched_kw) / len(keywords) if keywords else 0.0
    for kw in matched_kw:
        signals.append(f"kw:{kw}")

    # 3. Token Jaccard + SequenceMatcher similarity against title & keywords.
    reference = f"{template['title']} {' '.join(keywords)}"
    ref_tokens = _tokenize(reference)
    union = tokens | ref_tokens
    jaccard = len(tokens & ref_tokens) / len(union) if union else 0.0

    title_sim = SequenceMatcher(None, norm_text[:300], _normalize(template["title"])).ratio()
    text_sim = 0.6 * jaccard + 0.4 * title_sim

    total = min(1.0, 0.40 * code_score + 0.45 * kw_score + 0.15 * text_sim)

    # 4. Tytuł wzorca w jednej z pierwszych linii (słowa kluczowe nie łapią odmiany:
    #    „dowód osobisty” vs „wydanie dowodu osobistego”).
    norm_title = _normalize(template["title"])
    head = [_normalize(line) for line in text.splitlines() if line.strip()][:_TITLE_LINES]
    title_line = max((SequenceMatcher(None, line, norm_title).ratio() for line in head), default=0)
    if title_line >= _TITLE_LINE_MATCH:
        signals.append("title")
        total = min(1.0, total + 0.5 * title_line)
    return round(total, 4), signals


#: Minimalny wynik dopasowania wzorca. Teksty zgodne ze wzorcem uzyskują ≥ 0,3, a wnioski
#: spoza katalogu (wszystkie 9 formularzy konkursowych) ≤ 0,18 — poniżej progu wzorzec nie jest
#: wybierany, bo cudzy wzorzec w zapytaniu prowadzi model do zmyślania pól (np. „liczba drzew”
#: we wniosku o numer porządkowy).
MIN_TEMPLATE_SCORE = 0.25


def classify_document(
    text: str,
    *,
    registry: list[dict[str, Any]] | None = None,
    top_k: int = 5,
) -> dict[str, Any]:
    """Identify document type, returning top-5 scored candidates and best template."""
    templates = registry if registry is not None else TEMPLATE_REGISTRY
    scored: list[tuple[float, dict[str, Any], list[str]]] = []

    for tpl in templates:
        score, signals = _score_template(text, tpl)
        scored.append((score, tpl, signals))

    scored.sort(key=lambda item: (-item[0], item[1]["template_id"]))
    top_items = scored[:top_k]

    candidates = [
        {
            "template_id": tpl["template_id"],
            "code": tpl["code"],
            "doc_type": tpl["doc_type"],
            "title": tpl["title"],
            "score": score,
            "matched_signals": signals,
        }
        for score, tpl, signals in top_items
    ]

    confident = bool(top_items) and top_items[0][0] >= MIN_TEMPLATE_SCORE
    best_template = deepcopy(top_items[0][1]) if confident else None
    best_doc_type = candidates[0]["doc_type"] if confident else "unknown"

    return {
        "doc_type": best_doc_type,
        "top_candidates": candidates,
        "selected_template": best_template,
    }


def _template_for(decision: TypeDecision, registry: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Wzorzec typu wybranego przez RAG + przykład formularza (jeśli baza go ma)."""
    if decision.doc_type is None:
        return None
    known = next((t for t in registry if t["doc_type"] == decision.doc_type), None)
    template = (
        deepcopy(known)
        if known
        else {
            # Typ utworzony z zatwierdzenia (pętla zwrotna) — spoza katalogu.
            "template_id": f"tpl_{decision.doc_type}",
            "code": "",
            "doc_type": decision.doc_type,
            "title": decision.title or decision.doc_type,
            "keywords": [],
            "supports_proxy": True,
            "default_authority": "Urząd właściwy",
            "subject_section_title": "Przedmiot wniosku",
            "expected_fields": [],
        }
    )
    template["example_form"] = decision.example
    return template


def run_step05(
    document: dict[str, Any],
    *,
    registry: list[dict[str, Any]] | None = None,
    decision: TypeDecision | None = None,
) -> dict[str, Any]:
    """Run Step 05 on a document payload (plain text or extracted elements).

    ``decision`` — rozstrzygnięcie z bazy wiedzy (RAG); bez niego typ wybiera dopasowanie
    leksykalne do katalogu (z progiem ``MIN_TEMPLATE_SCORE``).
    """
    raw_text = str(document.get("text", "")).strip()
    if not raw_text and isinstance(document.get("elements"), list):
        raw_text = "\n".join(
            str(el.get("text", "")).strip() for el in document["elements"] if el.get("text")
        )
    if document.get("title"):
        raw_text = f"{document['title']}\n{raw_text}".strip()

    result = classify_document(raw_text, registry=registry, top_k=5)
    if decision is None:
        result["method"] = "lexical"
        return result
    templates = registry if registry is not None else TEMPLATE_REGISTRY
    result["doc_type"] = decision.doc_type or "unknown"
    result["selected_template"] = _template_for(decision, templates)
    result["method"] = decision.method
    return result
