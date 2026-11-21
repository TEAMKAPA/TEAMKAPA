"""Step 05: Document type recognition (top-5) and local COI template selection."""

from __future__ import annotations

import re
import unicodedata
from copy import deepcopy
from difflib import SequenceMatcher
from typing import Any

# Local registry of approved official Polish administrative form templates.
TEMPLATE_REGISTRY: list[dict[str, Any]] = [
    {
        "template_id": "tpl_dowod_osobisty",
        "code": "DO-01",
        "doc_type": "wniosek_dowod_osobisty",
        "title": "Wniosek o wydanie dowodu osobistego",
        "supports_proxy": False,
        "keywords": [
            "dowod osobisty",
            "wydanie dowodu",
            "pesel",
            "obywatelstwo",
            "powod zlozenia wniosku",
            "urzad gminy",
            "fotografia",
            "utrata dowodu",
        ],
        "default_authority": "Urząd Gminy / Urząd Miasta",
        "subject_section_title": "Powód złożenia wniosku i dane dowodu",
        "expected_fields": [
            "powod_wniosku",
            "fotografia",
            "obywatelstwo",
        ],
    },
    {
        "template_id": "tpl_ceidg_wpis",
        "code": "CEIDG-1",
        "doc_type": "wniosek_ceidg",
        "title": "Wniosek o wpis do ewidencji działalności gospodarczej",
        "supports_proxy": True,
        "keywords": [
            "ceidg",
            "dzialalnosc gospodarcza",
            "nip",
            "regon",
            "pkd",
            "forma opodatkowania",
            "nazwa firmy",
            "przedsiebiorca",
        ],
        "default_authority": "Urząd Miasta / CEIDG",
        "subject_section_title": "Dane działalności gospodarczej",
        "expected_fields": [
            "nazwa_firmy",
            "nip",
            "regon",
            "data_rozpoczecia",
            "kod_pkd",
            "forma_opodatkowania",
        ],
    },
    {
        "template_id": "tpl_usuniecie_drzewa",
        "code": "DRZ-01",
        "doc_type": "wniosek_usuniecie_drzewa",
        "title": "Wniosek o zezwolenie na usunięcie drzewa lub krzewu",
        "supports_proxy": True,
        "keywords": [
            "usuniecie drzewa",
            "drzewo",
            "krzew",
            "obwod pnia",
            "gatunek drzewa",
            "dzialka ewidencyjna",
            "zezwolenie na usuniecie",
            "nieruchomosc",
        ],
        "default_authority": "Wójt / Burmistrz / Prezydent Miasta",
        "subject_section_title": "Dane drzewa lub krzewu i nieruchomości",
        "expected_fields": [
            "numer_dzialki",
            "gatunek_drzewa",
            "obwod_pnia",
            "przyczyna_usuniecia",
            "plan_nasadzen",
        ],
    },
    {
        "template_id": "tpl_karta_duzej_rodziny",
        "code": "KDR-01",
        "doc_type": "wniosek_karta_duzej_rodziny",
        "title": "Wniosek o przyznanie Karty Dużej Rodziny",
        "supports_proxy": False,
        "keywords": [
            "karta duzej rodziny",
            "kdr",
            "rodzina wielodzietna",
            "czlonkowie rodziny",
            "dzieci",
            "tradycyjna karta",
            "elektroniczna karta",
            "stan cywilny",
        ],
        "default_authority": "Urząd Gminy / Ośrodek Pomocy Społecznej",
        "subject_section_title": "Członkowie rodziny i forma karty",
        "expected_fields": [
            "forma_karty",
            "liczba_dzieci",
            "dane_dziecka",
        ],
    },
    {
        "template_id": "tpl_urodzenie_dziecka",
        "code": "USC-UR",
        "doc_type": "zgloszenie_urodzenia_dziecka",
        "title": "Zgłoszenie urodzenia dziecka",
        "supports_proxy": False,
        "keywords": [
            "urodzenie dziecka",
            "akt urodzenia",
            "urzad stanu cywilnego",
            "imie dziecka",
            "plec dziecka",
            "data urodzenia dziecka",
            "zameldowanie dziecka",
            "karta urodzenia",
        ],
        "default_authority": "Urząd Stanu Cywilnego",
        "subject_section_title": "Dane dziecka",
        "expected_fields": [
            "imie_dziecka",
            "nazwisko_dziecka",
            "data_urodzenia_dziecka",
            "plec_dziecka",
        ],
    },
    {
        "template_id": "tpl_kanal_technologiczny",
        "code": "KT-01",
        "doc_type": "wniosek_kanal_technologiczny",
        "title": "Wniosek o zwolnienie z obowiązku budowy kanału technologicznego",
        "supports_proxy": True,
        "keywords": [
            "kanal technologiczny",
            "zwolnienie z obowiazku",
            "droga publiczna",
            "zarzadca drogi",
            "inwestycja drogowa",
            "lokalizacja inwestycji",
            "minister cyfryzacji",
        ],
        "default_authority": "Minister Cyfryzacji",
        "subject_section_title": "Dane inwestycji drogowej",
        "expected_fields": [
            "nazwa_inwestycji",
            "kategoria_drogi",
            "uzasadnienie_zwolnienia",
        ],
    },
]


def _normalize(text: str) -> str:
    replaced = text.lower().replace("ł", "l")
    decomposed = unicodedata.normalize("NFKD", replaced)
    cleaned = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", cleaned).strip()


def _tokenize(text: str) -> set[str]:
    return {tok for tok in re.findall(r"[a-z0-9]+", _normalize(text)) if len(tok) >= 2}


def _score_template(text: str, template: dict[str, Any]) -> tuple[float, list[str]]:
    norm_text = _normalize(text)
    tokens = _tokenize(text)
    signals: list[str] = []

    # 1. Form code exact match (strongest deterministic signal).
    code = template["code"]
    code_score = 0.0
    if re.search(rf"\b{re.escape(_normalize(code))}\b", norm_text):
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
    return round(total, 4), signals


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

    best_template = deepcopy(top_items[0][1]) if top_items else None
    best_doc_type = candidates[0]["doc_type"] if candidates else "unknown"

    return {
        "doc_type": best_doc_type,
        "top_candidates": candidates,
        "selected_template": best_template,
    }


def run_step05(
    document: dict[str, Any],
    *,
    registry: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Run Step 05 on a document payload (plain text or extracted elements)."""
    raw_text = str(document.get("text", "")).strip()
    if not raw_text and isinstance(document.get("elements"), list):
        raw_text = "\n".join(
            str(el.get("text", "")).strip() for el in document["elements"] if el.get("text")
        )
    if document.get("title"):
        raw_text = f"{document['title']}\n{raw_text}".strip()

    return classify_document(raw_text, registry=registry, top_k=5)
