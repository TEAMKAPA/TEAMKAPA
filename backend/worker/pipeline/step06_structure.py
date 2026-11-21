"""Step 06: Extract COI form tabs (MAIN + FINISH) and non-nested sections."""

from __future__ import annotations

from copy import deepcopy
import re
from typing import Any
import unicodedata
import uuid

from backend.worker.clients.llm_client import LLMClient

COI_SECTION_TYPES: frozenset[str] = frozenset({"SECTION", "REPEATING_SECTION"})

COI_INPUT_TYPES: frozenset[str] = frozenset(
    {
        "TEXT_FIELD",
        "CHECKBOX",
        "NUMBER",
        "CONSENT_SECTION",
        "SELECT",
        "TEXTAREA",
        "FILE_UPLOADER",
        "RADIO",
        "DATEPICKER",
    }
)

COI_COMPONENT_TYPES: frozenset[str] = (
    COI_INPUT_TYPES | COI_SECTION_TYPES | frozenset({"TEXT_BLOCK", "INFO_SECTION"})
)

COI_VALIDATORS: frozenset[str] = frozenset(
    {
        "REQUIRED",
        "EMAIL",
        "PESEL",
        "NIP",
        "REGON",
        "ZIP_CODE",
        "EPUAP",
        "ADE",
        "SERIAL_NUMBER_ID_CARD",
    }
)

COI_TITLE_PREFIXES: tuple[str, ...] = (
    "Wniosek",
    "Zgłoszenie",
    "Deklaracja",
    "Oświadczenie",
)

# Canonical logical COI ordering priority for sections.
_SECTION_PRIORITY: dict[str, int] = {
    "urzad": 10,
    "organ": 10,
    "podmiot_wniosku": 20,
    "twoje_dane": 30,
    "adres": 40,
    "dane_innej_osoby": 45,
    "dane_podmiotu": 50,
    "pelnomocnictwo": 55,
    "przedmiot_wniosku": 60,
    "dane_do_kontaktu": 70,
    "zgody": 80,
    "oswiadczenie": 80,
}

STRUCTURE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["title", "tabs"],
    "properties": {
        "title": {"type": "string", "minLength": 3, "maxLength": 255},
        "tabs": {
            "type": "array",
            "minItems": 2,
            "items": {
                "type": "object",
                "required": ["title", "type", "sections"],
                "properties": {
                    "title": {"type": "string", "minLength": 1},
                    "type": {"type": "string", "enum": ["MAIN", "FINISH"]},
                    "sections": {
                        "type": "array",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "required": ["title", "techName", "type"],
                            "properties": {
                                "title": {"type": "string", "minLength": 1},
                                "techName": {"type": "string", "minLength": 1},
                                "type": {
                                    "type": "string",
                                    "enum": [
                                        "SECTION",
                                        "REPEATING_SECTION",
                                        "INFO_SECTION",
                                    ],
                                },
                                "description": {"type": "string"},
                            },
                        },
                    },
                },
            },
        },
    },
}


def make_uuid(seed: str | None = None) -> str:
    """Create a deterministic UUID5 when a seed is given, otherwise UUID4."""
    if seed is not None:
        return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"coi.teamkapa.{seed}"))
    return str(uuid.uuid4())


def slugify_tech_name(text: str, fallback: str = "pole") -> str:
    """Convert Polish labels into clean snake_case COI techName identifiers."""
    replaced = text.lower().replace("ł", "l")
    decomposed = unicodedata.normalize("NFKD", replaced)
    ascii_text = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    slug = re.sub(r"[^a-z0-9]+", "_", ascii_text).strip("_")
    return slug or fallback


def normalize_form_title(raw_title: str, fallback: str = "Wniosek urzędowy") -> str:
    """Ensure title is concise, <= 255 chars, and starts with a COI prefix."""
    cleaned = re.sub(r"\s+", " ", raw_title).strip()
    if not cleaned:
        cleaned = fallback

    if not any(cleaned.startswith(prefix) for prefix in COI_TITLE_PREFIXES):
        cleaned = f"Wniosek — {cleaned}"

    return cleaned[:255]


def _section_order_key(tech_name: str, original_index: int) -> tuple[int, int]:
    for prefix, priority in _SECTION_PRIORITY.items():
        if tech_name.startswith(prefix) or prefix in tech_name:
            return (priority, original_index)
    return (65, original_index)


def _needs_repeating_subject(text: str) -> bool:
    norm = slugify_tech_name(text)
    hints = (
        "drzew",
        "krzew",
        "czlonk",
        "dzieci",
        "pojazd",
        "nieruchomosc",
        "wielu",
        "kolejn",
    )
    return any(hint in norm for hint in hints)


def _detect_supports_proxy(text: str, template: dict[str, Any] | None) -> bool:
    if template and template.get("supports_proxy"):
        return True
    norm = slugify_tech_name(text)
    return any(
        token in norm for token in ("pelnomocni", "w_imieniu", "podmiot", "reprezentan")
    )


def _default_finish_info_question(tab_seed: str, has_signature: bool = True) -> dict:
    preview_text = (
        "Zanim przejdziesz do wyboru sposobu podpisu, sprawdź dane na podglądzie. "
        "Jeśli zobaczysz błędy, wróć do edycji."
        if has_signature
        else "Zanim wyślesz formularz, sprawdź dane na podglądzie. "
        "Jeśli zobaczysz błędy, wróć do edycji."
    )
    return {
        "x": 0,
        "y": 0,
        "rows": 1,
        "cols": 12,
        "field": {
            "id": make_uuid(f"{tab_seed}.podglad"),
            "type": "INFO_SECTION",
            "techName": "podsumowanie_i_podglad",
            "title": "Podgląd",
            "description": preview_text,
            "states": [],
            "questionOrder": 1,
            "source_text": "Podsumowanie i podgląd formularza przed wysłaniem",
            "page": 1,
            "element_id": None,
        },
    }


def _build_mock_structure(
    document: dict[str, Any], template: dict[str, Any] | None
) -> dict[str, Any]:
    raw_text = str(document.get("text", ""))
    if not raw_text and isinstance(document.get("elements"), list):
        raw_text = " ".join(str(el.get("text", "")) for el in document["elements"])

    raw_title = str(
        document.get("title")
        or (template["title"] if template else "Wniosek urzędowy")
    )
    title = normalize_form_title(raw_title)

    is_organ = any(
        kw in slugify_tech_name(raw_text) for kw in ("wojt", "burmistrz", "prezydent")
    )
    authority_title = (
        "Organ, do którego jest składany wniosek"
        if is_organ
        else "Urząd, do którego jest składany wniosek"
    )
    authority_tech = "organ_wniosku" if is_organ else "urzad_wniosku"

    supports_proxy = _detect_supports_proxy(raw_text, template)
    tab1_sections: list[dict[str, Any]] = [
        {"title": authority_title, "techName": authority_tech, "type": "SECTION"},
    ]
    if supports_proxy:
        tab1_sections.append(
            {
                "title": "Podmiot wniosku",
                "techName": "podmiot_wniosku",
                "type": "SECTION",
            }
        )
    tab1_sections.extend(
        [
            {"title": "Twoje dane", "techName": "twoje_dane", "type": "SECTION"},
            {
                "title": "Adres zamieszkania",
                "techName": "adres_zamieszkania",
                "type": "SECTION",
            },
        ]
    )
    if supports_proxy:
        tab1_sections.extend(
            [
                {
                    "title": "Dane innej osoby",
                    "techName": "dane_innej_osoby",
                    "type": "SECTION",
                },
                {
                    "title": "Dane podmiotu",
                    "techName": "dane_podmiotu",
                    "type": "SECTION",
                },
                {
                    "title": "Pełnomocnictwo",
                    "techName": "pelnomocnictwo",
                    "type": "SECTION",
                },
            ]
        )

    subject_title = (
        template["subject_section_title"] if template else "Szczegóły sprawy"
    )
    subject_type = (
        "REPEATING_SECTION" if _needs_repeating_subject(raw_text) else "SECTION"
    )
    tab2_sections = [
        {
            "title": subject_title,
            "techName": "przedmiot_wniosku",
            "type": subject_type,
        }
    ]

    tab3_sections = [
        {
            "title": "Dane do kontaktu",
            "techName": "dane_do_kontaktu",
            "type": "SECTION",
        },
        {
            "title": "Zgody i oświadczenia",
            "techName": "zgody_i_oswiadczenia",
            "type": "SECTION",
        },
    ]

    finish_sections = [
        {
            "title": "Podgląd",
            "techName": "podsumowanie_i_podglad",
            "type": "INFO_SECTION",
            "description": (
                "Zanim przejdziesz do wyboru sposobu podpisu, sprawdź dane na "
                "podglądzie. Jeśli zobaczysz błędy, wróć do edycji."
            ),
        }
    ]

    return {
        "title": title,
        "tabs": [
            {"title": "Dane wnioskodawcy", "type": "MAIN", "sections": tab1_sections},
            {"title": "Przedmiot wniosku", "type": "MAIN", "sections": tab2_sections},
            {"title": "Kontakt i zgody", "type": "MAIN", "sections": tab3_sections},
            {
                "title": "Podsumowanie i podgląd",
                "type": "FINISH",
                "sections": finish_sections,
            },
        ],
    }


def _to_coi_tabs(raw_structure: dict[str, Any]) -> dict[str, Any]:
    title = normalize_form_title(str(raw_structure.get("title", "")))
    raw_tabs = raw_structure.get("tabs", [])

    main_tabs = [t for t in raw_tabs if t.get("type") != "FINISH"]
    finish_tabs = [t for t in raw_tabs if t.get("type") == "FINISH"]

    if not main_tabs:
        main_tabs = [
            {
                "title": "Dane wnioskodawcy",
                "type": "MAIN",
                "sections": [
                    {
                        "title": "Twoje dane",
                        "techName": "twoje_dane",
                        "type": "SECTION",
                    }
                ],
            }
        ]

    coi_tabs: list[dict[str, Any]] = []
    for tab_idx, raw_tab in enumerate(main_tabs):
        tab_title = str(raw_tab.get("title") or f"Strona {tab_idx + 1}").strip()
        tab_seed = f"tab.{tab_idx}.{slugify_tech_name(tab_title)}"
        sections = raw_tab.get("sections", [])

        ordered_sections = sorted(
            enumerate(sections),
            key=lambda pair: _section_order_key(
                slugify_tech_name(str(pair[1].get("techName") or pair[1].get("title"))),
                pair[0],
            ),
        )

        questions: list[dict[str, Any]] = []
        for q_idx, (_, sec) in enumerate(ordered_sections):
            sec_title = str(sec.get("title") or f"Sekcja {q_idx + 1}").strip()
            sec_tech = slugify_tech_name(str(sec.get("techName") or sec_title))
            sec_type = str(sec.get("type", "SECTION"))
            if sec_type not in COI_SECTION_TYPES:
                sec_type = "SECTION"

            field_obj: dict[str, Any] = {
                "id": make_uuid(f"{tab_seed}.{sec_tech}"),
                "type": sec_type,
                "techName": sec_tech,
                "title": sec_title,
                "repetingSectionGrid": [],
                "states": [],
                "questionOrder": q_idx + 1,
            }
            if sec.get("description"):
                field_obj["description"] = str(sec["description"])

            questions.append(
                {
                    "x": 0,
                    "y": q_idx,
                    "rows": 1,
                    "cols": 12,
                    "field": field_obj,
                }
            )

        coi_tabs.append(
            {
                "id": make_uuid(tab_seed),
                "title": tab_title,
                "type": "MAIN",
                "questions": questions,
            }
        )

    finish_raw = finish_tabs[-1] if finish_tabs else {}
    finish_title = str(finish_raw.get("title") or "Podsumowanie i podgląd").strip()
    finish_seed = f"tab.finish.{slugify_tech_name(finish_title)}"
    finish_questions: list[dict[str, Any]] = []

    for q_idx, sec in enumerate(finish_raw.get("sections", [])):
        if sec.get("type") != "INFO_SECTION":
            continue
        sec_title = str(sec.get("title") or "Podgląd").strip()
        sec_tech = slugify_tech_name(str(sec.get("techName") or sec_title))
        desc = str(
            sec.get("description")
            or (
                "Zanim przejdziesz do wyboru sposobu podpisu, sprawdź dane "
                "na podglądzie."
            )
        )
        finish_questions.append(
            {
                "x": 0,
                "y": q_idx,
                "rows": 1,
                "cols": 12,
                "field": {
                    "id": make_uuid(f"{finish_seed}.{sec_tech}"),
                    "type": "INFO_SECTION",
                    "techName": sec_tech,
                    "title": sec_title,
                    "description": desc,
                    "states": [],
                    "questionOrder": q_idx + 1,
                    "source_text": desc,
                    "page": 1,
                    "element_id": None,
                },
            }
        )

    if not finish_questions:
        finish_questions.append(_default_finish_info_question(finish_seed))

    coi_tabs.append(
        {
            "id": make_uuid(finish_seed),
            "title": finish_title,
            "type": "FINISH",
            "questions": finish_questions,
        }
    )

    return {"title": title, "tabs": coi_tabs, "flow": {"links": []}}


def validate_coi_structure(form_data: dict[str, Any]) -> list[str]:
    """Validate COI structural rules on a FormDefinition dictionary."""
    errors: list[str] = []

    title = form_data.get("title", "")
    if not isinstance(title, str) or not title.strip():
        errors.append("Form title must be a non-empty string.")
    elif len(title) > 255:
        errors.append(f"Form title exceeds 255 characters ({len(title)}).")

    tabs = form_data.get("tabs", [])
    if not isinstance(tabs, list) or len(tabs) < 2:
        errors.append("Form must have at least 2 tabs (MAIN + FINISH).")
        return errors

    for idx, tab in enumerate(tabs):
        tab_type = tab.get("type")
        is_last = idx == len(tabs) - 1
        questions = tab.get("questions", [])

        if is_last and tab_type != "FINISH":
            errors.append("Last tab must have type 'FINISH'.")
        if not is_last and tab_type != "MAIN":
            errors.append(f"Tab {idx} before FINISH must have type 'MAIN'.")
        if not questions:
            errors.append(f"Tab '{tab.get('title')}' must contain at least 1 item.")

        if tab_type == "FINISH":
            for q in questions:
                ftype = q.get("field", {}).get("type")
                if ftype != "INFO_SECTION":
                    errors.append(
                        f"FINISH tab may only contain INFO_SECTION, found '{ftype}'."
                    )
        else:
            for q in questions:
                field = q.get("field", {})
                ftype = field.get("type")
                if ftype not in COI_SECTION_TYPES:
                    errors.append(
                        f"MAIN tab top-level item must be SECTION or "
                        f"REPEATING_SECTION, found '{ftype}'."
                    )
                    continue
                for nested in field.get("repetingSectionGrid", []):
                    nested_type = nested.get("field", {}).get("type")
                    if nested_type in COI_SECTION_TYPES:
                        errors.append(
                            f"Nested section '{nested_type}' is forbidden inside "
                            f"section '{field.get('techName')}'."
                        )

    return errors


def run_step06(
    document: dict[str, Any],
    *,
    template: dict[str, Any] | None = None,
    llm_client: LLMClient | None = None,
) -> dict[str, Any]:
    """Extract COI-compliant tabs and sections from document and template context."""
    client = llm_client or LLMClient()
    tpl_title = template["title"] if template else "brak wzorca"
    raw_text = str(document.get("text", ""))[:1500]
    prompt = (
        "Wyekstrahuj strukturę stron (MAIN + FINISH) oraz sekcji formularza "
        f"zgodnie ze standardem COI.\nWzorzec: {tpl_title}\nTreść:\n{raw_text}"
    )

    llm_output = client.generate_structured(
        prompt,
        STRUCTURE_SCHEMA,
        mock_response_fn=lambda _family: _build_mock_structure(document, template),
    )

    form = _to_coi_tabs(deepcopy(llm_output))
    validation_errors = validate_coi_structure(form)
    if validation_errors:
        raise ValueError(f"Invalid COI structure: {'; '.join(validation_errors)}")
    return form
