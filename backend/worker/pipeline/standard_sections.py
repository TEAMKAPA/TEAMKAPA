"""Standardowe strony COI wstawiane z szablonu zamiast generowania modelem.

W obu referencyjnych konfiguracjach COI strona „Dane” (urząd/organ, podmiot wniosku, dane
wnioskodawcy, adres z przełącznikiem Polska/zagranica, dane podmiotu i siedziby, reprezentant,
pełnomocnik, opłata skarbowa) oraz „Kontakt i zgody” są niemal identyczne i stanowią ~85% pól
formularza. Większości z nich nie ma na papierowym wniosku — wynikają ze standardu e-usług
(„Standardy i dobre praktyki projektowania formularzy”), więc model nie może ich odczytać.

Szablon (``templates/coi_standard_sections.json``) pochodzi z plików wyeksportowanych z COI.
Model odpowiada za strony tematyczne (przedmiot wniosku) i nazwę urzędu/organu.

Pola i sekcje szablonu mają znacznik ``origin: "standard"``: etapy 07–09 ich nie zmieniają,
normalizacja przenosi znacznik do raportu, a ocena pewności nie szuka ich w dokumencie.
"""

import json
import re
import uuid
from copy import deepcopy
from functools import cache
from pathlib import Path
from typing import Any, Literal

ORIGIN_KEY = "origin"
ORIGIN_STANDARD = "standard"
GRID = "repeatingSectionGrid"
_COLS = 3

_TEMPLATE_PATH = Path(__file__).with_name("templates") / "coi_standard_sections.json"

AuthorityKind = Literal["ORGAN", "URZAD"]

# Sekcje, które zapewnia szablon — takie sekcje od modelu są pomijane. Dopasowanie po całych
# członach techName, żeby nie zgubić sekcji tematycznych (np. „organizacja_imprezy”,
# „zgodnosc_z_planem”, „oswiadczenie_o_niekaralnosci” zostają).
_STANDARD_NAMES = frozenset({"adres", "dane", "dane_osobowe"})
_STANDARD_PHRASES = (
    "podmiot_wniosku", "twoje_dane", "adres_zamieszkania", "dane_podmiotu", "adres_siedziby",
    "dane_innej_osoby", "oplata_skarbowa",
)  # fmt: skip
_STANDARD_TOKENS = frozenset(
    {"urzad", "urzedu", "organ", "organu", "wnioskodawca", "wnioskodawcy", "kontakt",
     "kontaktu", "kontaktowe", "zgoda", "zgody", "rodo", "podglad"}
)  # fmt: skip
_STANDARD_TOKEN_PREFIXES = ("pelnomocni", "reprezentant", "klauzul")

# Nazwa własna: wyrazy od wielkiej litery (bez względu na flagę IGNORECASE), w jednej linii.
_PROPER_NAME = r"(?-i:[A-ZĄĆĘŁŃÓŚŹŻ][\w-]*)(?:[ -](?-i:[A-ZĄĆĘŁŃÓŚŹŻ][\w-]*)){0,2}"
_ORGAN_RE = re.compile(
    r"\b(w[óo]jt|burmistrz|prezydent|starosta|marsza[łl]ek)[ \t]+"
    r"(gminy|miasta(?:[ \t]+i[ \t]+gminy)?|powiatu|wojew[óo]dztwa)[ \t]+" + _PROPER_NAME,
    re.IGNORECASE,
)
_OFFICE_RE = re.compile(
    r"\burz[ąa]d[ \t]+(gminy|miasta(?:[ \t]+i[ \t]+gminy)?|skarbowy|marsza[łl]kowski"
    r"|wojew[óo]dzki)(?:[ \t]+w)?[ \t]+" + _PROPER_NAME,
    re.IGNORECASE,
)


@cache
def _template() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(_TEMPLATE_PATH.read_text(encoding="utf-8"))
    return data


def _uuid(seed: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"coi.teamkapa.standard.{seed}"))


def _title_case(text: str) -> str:
    """„WÓJT GMINY PŁOSKINIA” → „Wójt Gminy Płoskinia”; nazwy pisane normalnie bez zmian."""

    def word(w: str) -> str:
        if w.lower() in {"i", "w"}:
            return w.lower()
        if not w.isupper():
            return w
        return "-".join(part[:1] + part[1:].lower() for part in w.split("-"))

    return " ".join(word(w) for w in text.split())


def find_authority(text: str) -> tuple[str, AuthorityKind] | None:
    """Nazwa organu (Wójt/Burmistrz/Prezydent…) albo urzędu z treści dokumentu."""
    organ = _ORGAN_RE.search(text)
    if organ:
        return _title_case(organ.group(0)), "ORGAN"
    office = _OFFICE_RE.search(text)
    if office:
        return _title_case(office.group(0)), "URZAD"
    return None


def is_standard_section(tech_name: str) -> bool:
    """Czy sekcja od modelu dubluje sekcję ze standardowego szablonu COI."""
    name = tech_name.lower()
    tokens = name.split("_")
    return (
        name in _STANDARD_NAMES
        or any(phrase in name for phrase in _STANDARD_PHRASES)
        or any(token in _STANDARD_TOKENS for token in tokens)
        or any(token.startswith(_STANDARD_TOKEN_PREFIXES) for token in tokens)
    )


def is_standard(element: dict[str, Any]) -> bool:
    return element.get(ORIGIN_KEY) == ORIGIN_STANDARD


def _mark(section: dict[str, Any], seed: str) -> dict[str, Any]:
    """Świeże identyfikatory i znacznik pochodzenia dla sekcji szablonu i jej pól."""
    section = deepcopy(section)
    section["id"] = _uuid(f"{seed}.{section['techName']}")
    section[ORIGIN_KEY] = ORIGIN_STANDARD
    for child in section.get(GRID, []):
        field = child["field"]
        field["id"] = _uuid(f"{seed}.{section['techName']}.{field['techName']}")
        field[ORIGIN_KEY] = ORIGIN_STANDARD
    return section


def _questions(items: list[dict[str, Any]], seed: str) -> list[dict[str, Any]]:
    questions = []
    y = 0
    for item in items:
        question: dict[str, Any] = {"x": 0, "y": y, "rows": item["rows"], "cols": _COLS}
        if "minItemRows" in item:
            question["minItemRows"] = item["minItemRows"]
        question["field"] = _mark(item["field"], seed)
        questions.append(question)
        y += item["rows"]
    return questions


def authority_section(name: str | None, kind: AuthorityKind) -> dict[str, Any]:
    """Sekcja „Urząd/Organ, do którego jest składany wniosek” z nazwą tylko do odczytu."""
    if kind == "ORGAN":
        tech, title, field_tech, field_title = (
            "organ_do_ktorego_jest_skladany_wniosek",
            "Organ, do którego jest składany wniosek",
            "nazwa_organu",
            "Nazwa organu",
        )
    else:
        tech, title, field_tech, field_title = (
            "urzad_do_ktorego_jest_skladany_wniosek",
            "Urząd, do którego jest składany wniosek",
            "nazwa_urzedu",
            "Nazwa urzędu",
        )
    field: dict[str, Any] = {
        "type": "TEXT_FIELD",
        "techName": field_tech,
        "title": field_title,
        "validation": [],
        # Bez nazwy w dokumencie pole zostaje edytowalne — urzędnik uzupełnia je przed importem.
        "readOnly": bool(name),
        "states": [],
        "questionOrder": 0,
    }
    if name:
        field["defaultValue"] = name
    return {
        "type": "SECTION",
        "techName": tech,
        "title": title,
        "states": [],
        GRID: [{"x": 0, "y": 0, "rows": 1, "cols": _COLS, "field": field}],
    }


def standard_dane_tab(authority: tuple[str, AuthorityKind] | None) -> dict[str, Any]:
    template = _template()
    name: str | None = authority[0] if authority else None
    kind: AuthorityKind = authority[1] if authority else "URZAD"
    items = [{"rows": 2, "field": authority_section(name, kind)}, *template["dane_sections"]]
    title = str(template["dane_tab_title"])
    return {
        "id": _uuid(f"tab.{title}"),
        "title": title,
        "type": "MAIN",
        "questions": _questions(items, "dane"),
        ORIGIN_KEY: ORIGIN_STANDARD,
    }


def standard_contact_tab() -> dict[str, Any]:
    template = _template()
    title = str(template["contact_tab_title"])
    return {
        "id": _uuid(f"tab.{title}"),
        "title": title,
        "type": "MAIN",
        "questions": _questions(template["contact_sections"], "kontakt"),
        ORIGIN_KEY: ORIGIN_STANDARD,
    }


def standard_tab_titles() -> frozenset[str]:
    template = _template()
    return frozenset({str(template["dane_tab_title"]), str(template["contact_tab_title"])})
