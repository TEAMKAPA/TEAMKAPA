"""Zapytania do modelu językowego dla etapów 06–08.

Model dostaje to, czego potrzebuje do odpowiedzi — bez tego zgaduje:
- treść dokumentu jako listę elementów ``[id|s.strona] tekst`` (identyfikatory i strony wracają
  w odpowiedzi jako źródło pola i pozwalają ocenić pewność),
- zasady COI wyprowadzone z materiałów organizatora i referencyjnych plików COI,
- kontekst poprzednich etapów (sekcje dla etapu 07, pola i opcje dla etapu 08),
- schemat odpowiedzi (JSON Schema) — odpowiedź jest z nim sprawdzana i w razie błędu
  ponawiana z opisem błędów (``LLMClient.generate_structured``).
"""

import json
from typing import Any

#: Limit treści dokumentu w zapytaniu (znaki). Najdłuższe formularze konkursowe mają ~21 tys.
MAX_DOCUMENT_CHARS = 60_000

SYSTEM_PROMPT = (
    "Jesteś analitykiem cyfryzacji formularzy urzędowych w systemie e-usług COI. "
    "Zamieniasz papierowe wnioski na konfigurację e-formularza. Odpowiadasz wyłącznie "
    "obiektem JSON zgodnym z podanym schematem — bez komentarzy i bez bloków kodu."
)

_GRID = "repeatingSectionGrid"


def document_listing(document: dict[str, Any], max_chars: int = MAX_DOCUMENT_CHARS) -> str:
    """Treść dokumentu jako elementy ``[element_id|s.strona] tekst`` (z limitem długości)."""
    elements = document.get("elements") or []
    if elements:
        lines = [
            f"[{e.get('element_id')}|s.{e.get('page')}] {' '.join(str(e.get('text', '')).split())}"
            for e in elements
            if str(e.get("text", "")).strip()
        ]
    else:
        lines = [line for line in str(document.get("text", "")).splitlines() if line.strip()]
    listing: list[str] = []
    used = 0
    for line in lines:
        if used + len(line) > max_chars:
            listing.append(f"(… pominięto {len(lines) - len(listing)} elementów — limit długości)")
            break
        listing.append(line)
        used += len(line) + 1
    return "\n".join(listing)


def schema_instruction(schema: dict[str, Any]) -> str:
    return (
        "Odpowiedz wyłącznie obiektem JSON zgodnym z tym schematem (JSON Schema):\n"
        + json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
    )


# ------------------------------------------------------------------ etap 06: strony i sekcje

_STRUCTURE_RULES = """\
Zasady COI dla stron i sekcji:
- System sam dodaje standardowe strony COI: „Dane” (urząd, podmiot wniosku, dane i adres
  wnioskodawcy, dane innej osoby lub podmiotu, reprezentant, pełnomocnictwo, opłata skarbowa
  za pełnomocnictwo), „Kontakt i zgody” (telefon, e-mail, zgoda RODO) oraz „Strona końcowa”.
  Nie twórz tych stron ani sekcji o takiej treści.
- Zwróć wyłącznie strony tematyczne (type MAIN) z przedmiotem wniosku, nazwane od treści
  (np. „Informacje o budynku”, „Informacje o nadpłacie”, „Dane psa”) — zwykle jedną;
  kilka tylko przy długim formularzu z wyraźnie odrębnymi częściami.
- Sekcja grupuje powiązane pola (np. „Lokalizacja budynku”, „Rozliczenie nadpłaty”).
  Sekcja, której wierszy może być wiele (np. lista osób, nieruchomości), ma typ
  REPEATING_SECTION.
- techName: snake_case, bez polskich znaków, unikalny w formularzu.
- Nie twórz sekcji dla: podpisu, daty i miejsca złożenia, pieczęci, pól „wypełnia urząd”,
  instrukcji wypełniania ani klauzuli informacyjnej RODO.
- authority: pełna nazwa organu albo urzędu, do którego składany jest wniosek, dokładnie jak
  w dokumencie (np. „Wójt Gminy Elbląg”, „Urząd Gminy Ulan-Majorat”); kind ORGAN dla wójta,
  burmistrza, prezydenta miasta, starosty; URZAD dla urzędu.
- Tytuł formularza zaczyna się od rodzaju dokumentu („Wniosek o …”, „Deklaracja …”,
  „Zgłoszenie …”, „Oświadczenie …”), maks. 255 znaków."""


def structure_prompt(
    document: dict[str, Any], template_title: str | None, schema: dict[str, Any]
) -> str:
    return "\n\n".join(
        [
            "Zaprojektuj strony i sekcje e-formularza dla poniższego wniosku papierowego.",
            _STRUCTURE_RULES,
            f"Rozpoznany rodzaj dokumentu: {template_title or 'nieznany'}",
            "Treść dokumentu (elementy: [id|strona] tekst):\n" + document_listing(document),
            schema_instruction(schema),
        ]
    )


# ------------------------------------------------------------------ etap 07: pola

_FIELD_RULES = """\
Zasady COI dla pól:
- Typy: TEXT_FIELD (krótki tekst), TEXTAREA (dłuższy opis, uzasadnienie), NUMBER (liczba,
  kwota, powierzchnia), DATEPICKER (data), RADIO (jedna z 2–5 opcji), SELECT (jedna z wielu
  opcji albo słownik), CHECKBOX (wiele opcji naraz), FILE_UPLOADER (załącznik),
  CONSENT_SECTION (zgoda lub oświadczenie do zaakceptowania — nigdy CHECKBOX ani RADIO),
  TEXT_BLOCK (informacja do przeczytania, np. o wysokości opłaty).
- validation: REQUIRED, gdy pole jest obowiązkowe (brak dopisku „nieobowiązkowe”,
  „opcjonalnie”, „jeśli dotyczy”). Dla TEXT_FIELD dodatkowo: EMAIL, PESEL, NIP, REGON,
  ZIP_CODE (kod pocztowy), EPUAP, ADE, SERIAL_NUMBER_ID_CARD (seria i numer dowodu) —
  tylko gdy pole dokładnie tego dotyczy.
- Słownik zamiast opcji: kraj → SELECT, dictionaryName „Kraje”, defaultValue „Polska”
  (pole ze słownikiem ma puste options).
- Dane wnioskodawcy, jego adres, PESEL, dane pełnomocnika lub reprezentanta, telefon, e-mail,
  zgoda RODO i nazwa urzędu są w standardowych sekcjach COI — nie zwracaj takich pól.
  Zwracaj pola dotyczące przedmiotu wniosku (np. położenie nieruchomości, kwota, uzasadnienie,
  załączniki wymagane przez wniosek, oświadczenia właściwe dla sprawy).
- options: nazwy opcji dokładnie jak w dokumencie (pole wyboru), bez znaków □ ☐ ⃞.
- Pomiń: podpis, datę i miejsce złożenia, pola „wypełnia urząd”, pieczęcie, nagłówki,
  numerację, instrukcje wypełniania, klauzulę RODO.
- source_text: dosłowna etykieta pola z dokumentu (maks. 80 znaków); page i element_id:
  strona i identyfikator elementu, z którego pochodzi etykieta.
- section_tech_name: techName jednej z podanych sekcji; title: krótka etykieta pola
  w e-formularzu (np. „Imię”, „Kod pocztowy”); techName: snake_case bez polskich znaków."""


def _sections_listing(form: dict[str, Any]) -> str:
    lines = []
    for tab in form.get("tabs", []):
        if tab.get("type") != "MAIN" or tab.get("origin") == "standard":
            continue  # strony ze standardowego szablonu COI mają już pola
        for question in tab.get("questions", []):
            section = question.get("field", {})
            lines.append(
                f"- {section.get('techName')}: „{section.get('title')}” "
                f"(strona „{tab.get('title')}”, {section.get('type')})"
            )
    return "\n".join(lines)


def fields_prompt(
    document: dict[str, Any],
    structure: dict[str, Any],
    template_title: str | None,
    schema: dict[str, Any],
) -> str:
    return "\n\n".join(
        [
            "Wyodrębnij pola e-formularza z poniższego wniosku papierowego i przypisz je do "
            "sekcji. Zwróć wszystkie pola do wypełnienia przez wnioskodawcę.",
            _FIELD_RULES,
            f"Rozpoznany rodzaj dokumentu: {template_title or 'nieznany'}",
            "Sekcje formularza (techName: tytuł):\n" + _sections_listing(structure),
            "Treść dokumentu (elementy: [id|strona] tekst):\n" + document_listing(document),
            schema_instruction(schema),
        ]
    )


# ------------------------------------------------------------------ etap 08: zależności

_DEPENDENCY_RULES = """\
Zasady zależności (stany COI):
- SHOW: sekcja lub pole jest widoczne tylko przy określonej odpowiedzi na pole wyboru
  (RADIO, SELECT, CHECKBOX). READ_ONLY: pole tylko do odczytu przy określonej odpowiedzi.
- source_field_tech_name: techName pola wyboru, od którego zależy element;
  expected_value: nazwa opcji dokładnie jak na liście opcji tego pola (dla pola ze słownikiem
  „Kraje” — nazwa kraju, np. „Polska”); negated: true, gdy element jest widoczny dla
  każdej odpowiedzi OPRÓCZ expected_value.
- Typowe zależności: dane innej osoby lub podmiotu widoczne, gdy wnioskodawca wybrał
  działanie w ich imieniu; pola adresu krajowego widoczne dla kraju „Polska”, a pola adresu
  zagranicznego — dla pozostałych krajów (negated); pole „inne/jakie” widoczne po wybraniu
  opcji „Inne”; załącznik widoczny, gdy odpowiedź wskazuje jego potrzebę.
- Zwracaj tylko zależności wynikające z treści dokumentu lub z powyższych wzorców.
  Element nie może zależeć od pola z własnej sekcji (dla sekcji) ani od samego siebie.
  Brak zależności → pusta lista."""


def _fields_listing(form: dict[str, Any]) -> str:
    lines = []
    for tab in form.get("tabs", []):
        if tab.get("type") != "MAIN":
            continue
        for question in tab.get("questions", []):
            section = question.get("field", {})
            lines.append(f"sekcja {section.get('techName')}: „{section.get('title')}”")
            for child in section.get(_GRID, []):
                field = child.get("field", {})
                options = [o.get("name") for o in field.get("options") or []]
                extra = f" opcje: {options}" if options else ""
                if field.get("dictionaryName"):
                    extra = f" słownik: {field['dictionaryName']}"
                lines.append(
                    f"  - {field.get('techName')} ({field.get('type')}): "
                    f"„{field.get('title')}”{extra}"
                )
    return "\n".join(lines)


def dependencies_prompt(
    document: dict[str, Any] | None, form: dict[str, Any], schema: dict[str, Any]
) -> str:
    parts = [
        f"Wskaż zależności warunkowe w e-formularzu „{form.get('title', '')}”.",
        _DEPENDENCY_RULES,
        "Sekcje i pola formularza (techName (TYP): tytuł, opcje):\n" + _fields_listing(form),
    ]
    if document:
        parts.append(
            "Treść dokumentu (elementy: [id|strona] tekst):\n" + document_listing(document)
        )
    parts.append(schema_instruction(schema))
    return "\n\n".join(parts)
