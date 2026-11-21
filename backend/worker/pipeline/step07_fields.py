"""Step 07: Extract fields with COI component types, validators, and source metadata."""

from __future__ import annotations

import re
import unicodedata
from copy import deepcopy
from typing import Any

from backend.worker.clients.llm_client import LLMClient
from backend.worker.pipeline.step06_structure import (
    COI_COMPONENT_TYPES,
    COI_SECTION_TYPES,
    COI_VALIDATORS,
    make_uuid,
    slugify_tech_name,
)

FIELDS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["fields"],
    "properties": {
        "fields": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": [
                    "section_tech_name",
                    "techName",
                    "title",
                    "type",
                    "source_text",
                    "page",
                ],
                "properties": {
                    "section_tech_name": {"type": "string", "minLength": 1},
                    "techName": {"type": "string", "minLength": 1},
                    "title": {"type": "string", "minLength": 1},
                    "type": {
                        "type": "string",
                        "enum": sorted(COI_COMPONENT_TYPES - COI_SECTION_TYPES),
                    },
                    "description": {"type": "string"},
                    "validation": {
                        "type": "array",
                        "items": {"type": "string", "enum": sorted(COI_VALIDATORS)},
                    },
                    "dictionaryName": {"type": ["string", "null"]},
                    "readOnly": {"type": "boolean"},
                    "defaultValue": {"type": "string"},
                    "defaultValueType": {"type": "string"},
                    "options": {"type": "array", "items": {"type": "string"}},
                    "source_text": {"type": "string", "minLength": 1},
                    "page": {"type": "integer", "minimum": 1},
                    "element_id": {"type": ["string", "null"]},
                },
            },
        }
    },
}

_CONSENT_RE = re.compile(
    r"\b(?:zgod[ayie]|wyrazam\s+zgode|oswiadcz\w*|klauzul\w*|rodo|"
    r"przetwarzani\w*\s+danych|regulamin\w*)\b"
)
_FILE_RE = re.compile(r"\b(?:zalacznik\w*|plik\w*|fotografi\w*|skan\w*|dokument\w*)\b")
_DATE_RE = re.compile(r"\b(?:dat[ayie]|termin\w*)\b")
_TEXTAREA_RE = re.compile(r"\b(?:uzasadnieni\w*|opis\w*|uwag\w*|przyczyn\w*|wyjasnieni\w*)\b")
_NUMBER_RE = re.compile(r"\b(?:liczba|ilosc|obwod|powierzchni\w*|kwot\w*|wysokosc|wiek)\b")


def _norm(text: str) -> str:
    replaced = text.lower().replace("ł", "l")
    decomposed = unicodedata.normalize("NFKD", replaced)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def _is_consent_text(text: str) -> bool:
    return bool(_CONSENT_RE.search(_norm(text)))


def _infer_coi_type_and_validators(
    label: str,
    *,
    options: list[str] | None = None,
    required: bool = True,
    requested_type: str | None = None,
) -> tuple[str, list[str], str | None]:
    norm = _norm(label)
    opts = options or []

    # COI strict rule: consents & declarations ALWAYS use CONSENT_SECTION.
    if _is_consent_text(label):
        return "CONSENT_SECTION", ["REQUIRED"] if required else [], None

    # Official COI dictionaries -> SELECT.
    if "wojewodztw" in norm:
        return "SELECT", ["REQUIRED"] if required else [], "Województwo"
    if "powiat" in norm:
        return "SELECT", ["REQUIRED"] if required else [], "Powiat"
    if "gmin" in norm:
        return "SELECT", ["REQUIRED"] if required else [], "Gmina"
    if "miejscowosc" in norm or re.search(r"\bmiast[oa]\b", norm):
        return "SELECT", ["REQUIRED"] if required else [], "Miejscowość"
    if any(k in norm for k in ("kraj", "obywatelstw", "panstw")):
        return "SELECT", ["REQUIRED"] if required else [], "Kraje"
    if "kierunkow" in norm:
        return "SELECT", ["REQUIRED"] if required else [], "Numery kierunkowe"

    # Official COI domain validators.
    validators: list[str] = ["REQUIRED"] if required else []
    if "pesel" in norm:
        return "TEXT_FIELD", [*validators, "PESEL"], None
    if re.search(r"\bnip\b", norm):
        return "TEXT_FIELD", [*validators, "NIP"], None
    if "regon" in norm:
        return "TEXT_FIELD", [*validators, "REGON"], None
    if "kod pocztow" in norm:
        return "TEXT_FIELD", [*validators, "ZIP_CODE"], None
    if "e-mail" in norm or "email" in norm or "poczta elektroniczn" in norm:
        return "TEXT_FIELD", [*validators, "EMAIL"], None
    if "epuap" in norm:
        return "TEXT_FIELD", [*validators, "EPUAP"], None
    if "e-doreczen" in norm or re.search(r"\bade\b", norm):
        return "TEXT_FIELD", [*validators, "ADE"], None
    if "seria i numer dowodu" in norm or "numer dowodu" in norm:
        return "TEXT_FIELD", [*validators, "SERIAL_NUMBER_ID_CARD"], None

    if _FILE_RE.search(norm):
        return "FILE_UPLOADER", validators, None
    if _DATE_RE.search(norm):
        return "DATEPICKER", validators, None
    if _TEXTAREA_RE.search(norm):
        return "TEXTAREA", validators, None
    if _NUMBER_RE.search(norm):
        return "NUMBER", validators, None

    if opts:
        field_type = "SELECT" if len(opts) > 5 else "RADIO"
        if requested_type in {"RADIO", "CHECKBOX", "SELECT"}:
            field_type = requested_type
        return field_type, validators, None

    if requested_type in (COI_COMPONENT_TYPES - COI_SECTION_TYPES):
        return requested_type, validators, None

    return "TEXT_FIELD", validators, None


def _match_source_element(
    label: str, elements: list[dict[str, Any]]
) -> tuple[str, int, str | None]:
    norm_label = _norm(label)
    label_tokens = {t for t in re.findall(r"[a-z0-9]+", norm_label) if len(t) >= 3}

    best: tuple[int, dict[str, Any]] | None = None
    for el in elements:
        el_text = str(el.get("text", "")).strip()
        if not el_text:
            continue
        norm_el = _norm(el_text)
        if norm_label and norm_label in norm_el:
            return (
                el_text,
                max(1, int(el.get("page", 1))),
                el.get("element_id"),
            )
        el_tokens = {t for t in re.findall(r"[a-z0-9]+", norm_el) if len(t) >= 3}
        overlap = len(label_tokens & el_tokens)
        if overlap > 0 and (best is None or overlap > best[0]):
            best = (overlap, el)

    if best is not None:
        el = best[1]
        return (
            str(el.get("text", label)).strip() or label,
            max(1, int(el.get("page", 1))),
            el.get("element_id"),
        )
    return (label, 1, None)


def _extract_elements(document: dict[str, Any]) -> list[dict[str, Any]]:
    raw_elements = document.get("elements")
    if isinstance(raw_elements, list) and raw_elements:
        return [el for el in raw_elements if isinstance(el, dict)]

    text = str(document.get("text", "")).strip()
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return [
        {"element_id": f"line_{idx + 1}", "page": 1, "text": line} for idx, line in enumerate(lines)
    ]


def _build_mock_fields(
    document: dict[str, Any],
    template: dict[str, Any] | None,
    structure: dict[str, Any],
) -> dict[str, Any]:
    """Deterministic offline extraction for tests and demo mode."""
    elements = _extract_elements(document)
    section_names = {
        q["field"]["techName"]
        for tab in structure.get("tabs", [])
        if tab.get("type") == "MAIN"
        for q in tab.get("questions", [])
    }

    fields: list[dict[str, Any]] = []

    def add_field(
        section: str,
        tech: str,
        title: str,
        ftype: str,
        *,
        validation: list[str] | None = None,
        dictionary: str | None = None,
        read_only: bool = False,
        default_value: str | None = None,
        default_value_type: str | None = None,
        options: list[str] | None = None,
        source_hint: str | None = None,
    ) -> None:
        if section not in section_names:
            return
        src_text, page, elem_id = _match_source_element(source_hint or title, elements)
        entry: dict[str, Any] = {
            "section_tech_name": section,
            "techName": tech,
            "title": title,
            "type": ftype,
            "validation": validation if validation is not None else [],
            "readOnly": read_only,
            "source_text": src_text,
            "page": page,
            "element_id": elem_id,
        }
        if dictionary:
            entry["dictionaryName"] = dictionary
        if default_value is not None:
            entry["defaultValue"] = default_value
        if default_value_type is not None:
            entry["defaultValueType"] = default_value_type
        if options:
            entry["options"] = options
        fields.append(entry)

    # 1. Authority section (readOnly).
    authority = template["default_authority"] if template else "Urząd właściwy"
    if "organ_wniosku" in section_names:
        add_field(
            "organ_wniosku",
            "nazwa_organu",
            "Nazwa organu",
            "TEXT_FIELD",
            read_only=True,
            default_value=authority,
            source_hint="Organ",
        )
    elif "urzad_wniosku" in section_names:
        add_field(
            "urzad_wniosku",
            "nazwa_urzedu",
            "Nazwa urzędu",
            "TEXT_FIELD",
            read_only=True,
            default_value=authority,
            source_hint="Urząd",
        )

    # 2. Podmiot wniosku (if present).
    if "podmiot_wniosku" in section_names:
        add_field(
            "podmiot_wniosku",
            "w_czyim_imieniu",
            "Wybierz, w czyim imieniu wnioskujesz",
            "RADIO",
            validation=["REQUIRED"],
            options=[
                "Swoim",
                "Innej osoby",
                "Osoby prawnej lub jednostki organizacyjnej",
            ],
        )
        add_field(
            "podmiot_wniosku",
            "jako_kto_wnioskujesz",
            "Wybierz, jako kto wnioskujesz",
            "RADIO",
            validation=["REQUIRED"],
            options=["Pełnomocnik", "Reprezentant"],
        )

    # 3. Twoje dane (readOnly + EXTERNAL_SYSTEM).
    add_field(
        "twoje_dane",
        "imie",
        "Imię",
        "TEXT_FIELD",
        validation=["REQUIRED"],
        read_only=True,
        default_value_type="EXTERNAL_SYSTEM",
    )
    add_field(
        "twoje_dane",
        "nazwisko",
        "Nazwisko",
        "TEXT_FIELD",
        validation=["REQUIRED"],
        read_only=True,
        default_value_type="EXTERNAL_SYSTEM",
    )
    add_field(
        "twoje_dane",
        "pesel",
        "Numer PESEL",
        "TEXT_FIELD",
        validation=["REQUIRED", "PESEL"],
        read_only=True,
        default_value_type="EXTERNAL_SYSTEM",
    )

    # 4. Adres zamieszkania.
    add_field(
        "adres_zamieszkania",
        "kraj",
        "Kraj",
        "SELECT",
        validation=["REQUIRED"],
        dictionary="Kraje",
        default_value="Polska",
    )
    add_field(
        "adres_zamieszkania",
        "wojewodztwo",
        "Województwo",
        "SELECT",
        validation=["REQUIRED"],
        dictionary="Województwo",
    )
    add_field(
        "adres_zamieszkania",
        "miejscowosc",
        "Miejscowość",
        "SELECT",
        validation=["REQUIRED"],
        dictionary="Miejscowość",
    )
    add_field(
        "adres_zamieszkania",
        "kod_pocztowy",
        "Kod pocztowy",
        "TEXT_FIELD",
        validation=["REQUIRED", "ZIP_CODE"],
    )
    add_field(
        "adres_zamieszkania",
        "ulica",
        "Ulica",
        "TEXT_FIELD",
        validation=["REQUIRED"],
    )
    add_field(
        "adres_zamieszkania",
        "numer_budynku",
        "Numer budynku",
        "TEXT_FIELD",
        validation=["REQUIRED"],
    )

    # 5. Conditional proxy sections if present.
    if "dane_innej_osoby" in section_names:
        add_field(
            "dane_innej_osoby",
            "imie_innej_osoby",
            "Imię",
            "TEXT_FIELD",
            validation=["REQUIRED"],
        )
        add_field(
            "dane_innej_osoby",
            "nazwisko_innej_osoby",
            "Nazwisko",
            "TEXT_FIELD",
            validation=["REQUIRED"],
        )
        add_field(
            "dane_innej_osoby",
            "pesel_innej_osoby",
            "Numer PESEL",
            "TEXT_FIELD",
            validation=["REQUIRED", "PESEL"],
        )
    if "dane_podmiotu" in section_names:
        add_field(
            "dane_podmiotu",
            "nazwa_podmiotu",
            "Nazwa podmiotu",
            "TEXT_FIELD",
            validation=["REQUIRED"],
        )
        add_field(
            "dane_podmiotu",
            "nip_podmiotu",
            "NIP",
            "TEXT_FIELD",
            validation=["REQUIRED", "NIP"],
        )
        add_field(
            "dane_podmiotu",
            "regon_podmiotu",
            "REGON",
            "TEXT_FIELD",
            validation=["REQUIRED", "REGON"],
        )
    if "pelnomocnictwo" in section_names:
        add_field(
            "pelnomocnictwo",
            "plik_pelnomocnictwa",
            "Plik dokumentu z pełnomocnictwem",
            "FILE_UPLOADER",
            validation=["REQUIRED"],
        )

    # 6. Subject-specific fields from document elements.
    added_techs = {f["techName"] for f in fields}
    skip_tokens = (
        "imie",
        "nazwisko",
        "pesel",
        "wojewodztw",
        "powiat",
        "gmin",
        "miejscowosc",
        "kod pocztow",
        "ulica",
        "numer budynku",
        "telefon",
        "e-mail",
        "email",
        "urzad",
        "organ",
        "podsumowanie",
    )
    consent_elements: list[dict[str, Any]] = []
    subject_count = 0

    for el in elements:
        el_text = str(el.get("text", "")).strip()
        if not el_text:
            continue
        if _is_consent_text(el_text):
            consent_elements.append(el)
            continue

        el_label = str(el.get("label") or el_text.split(":", 1)[0]).strip()
        norm_el = _norm(el_label)
        if any(tok in norm_el for tok in skip_tokens):
            continue
        if len(el_label) < 3 or len(el_label) > 120:
            continue

        tech = slugify_tech_name(el_label)
        if tech in added_techs:
            continue

        opts = el.get("options") if isinstance(el.get("options"), list) else []
        req = bool(el.get("required", True))
        ftype, vals, dict_name = _infer_coi_type_and_validators(
            el_label, options=opts, required=req, requested_type=el.get("type")
        )

        add_field(
            "przedmiot_wniosku",
            tech,
            el_label,
            ftype,
            validation=vals,
            dictionary=dict_name,
            options=opts,
            source_hint=el_text,
        )
        added_techs.add(tech)
        subject_count += 1

    if subject_count == 0 and "przedmiot_wniosku" in section_names:
        add_field(
            "przedmiot_wniosku",
            "opis_sprawy",
            "Opis przedmiotu wniosku",
            "TEXTAREA",
            validation=["REQUIRED"],
        )

    # 7. Dane do kontaktu (optional by COI standard).
    add_field(
        "dane_do_kontaktu",
        "numer_telefonu",
        "Numer telefonu",
        "TEXT_FIELD",
        validation=[],
        default_value_type="EXTERNAL_SYSTEM",
    )
    add_field(
        "dane_do_kontaktu",
        "adres_email",
        "Adres e-mail",
        "TEXT_FIELD",
        validation=["EMAIL"],
        default_value_type="EXTERNAL_SYSTEM",
    )

    # 8. Consents — ALWAYS CONSENT_SECTION (never CHECKBOX or RADIO).
    if consent_elements:
        for idx, el in enumerate(consent_elements):
            el_text = str(el.get("text", "")).strip()
            add_field(
                "zgody_i_oswiadczenia",
                f"zgoda_{idx + 1}",
                el_text[:120],
                "CONSENT_SECTION",
                validation=["REQUIRED"],
                source_hint=el_text,
            )
    else:
        default_consent = "Oświadczam, że podane we wniosku dane są zgodne z prawdą."
        add_field(
            "zgody_i_oswiadczenia",
            "oswiadczenie_prawdziwosci",
            default_consent,
            "CONSENT_SECTION",
            validation=["REQUIRED"],
            source_hint=default_consent,
        )

    return {"fields": fields}


def _to_coi_question_field(raw: dict[str, Any], order: int) -> dict[str, Any]:
    title = str(raw["title"]).strip()
    tech_name = slugify_tech_name(str(raw["techName"]))
    raw_type = str(raw["type"])

    # Hard COI rule: consents/declarations must ALWAYS be CONSENT_SECTION.
    if _is_consent_text(title) or raw_type == "CONSENT_SECTION":
        field_type = "CONSENT_SECTION"
    elif raw_type in (COI_COMPONENT_TYPES - COI_SECTION_TYPES):
        field_type = raw_type
    else:
        field_type = "TEXT_FIELD"

    raw_vals = raw.get("validation") or []
    validators = [v for v in raw_vals if v in COI_VALIDATORS]

    field_obj: dict[str, Any] = {
        "id": make_uuid(f"field.{raw['section_tech_name']}.{tech_name}"),
        "type": field_type,
        "techName": tech_name,
        "title": title,
        "validation": validators,
        "readOnly": bool(raw.get("readOnly", False)),
        "states": [],
        "questionOrder": order,
        "source_text": str(raw["source_text"]).strip() or title,
        "page": max(1, int(raw.get("page", 1))),
        "element_id": raw.get("element_id"),
    }

    if raw.get("description"):
        field_obj["description"] = str(raw["description"])
    if raw.get("dictionaryName"):
        field_obj["dictionaryName"] = str(raw["dictionaryName"])
        field_obj["options"] = []
    elif raw.get("options"):
        field_obj["options"] = [
            {
                "id": make_uuid(f"opt.{tech_name}.{slugify_tech_name(str(opt))}"),
                "name": str(opt),
                "techName": slugify_tech_name(str(opt)),
            }
            for opt in raw["options"]
        ]

    if raw.get("defaultValue") is not None:
        field_obj["defaultValue"] = str(raw["defaultValue"])
    if raw.get("defaultValueType"):
        field_obj["defaultValueType"] = str(raw["defaultValueType"])

    if field_type == "CONSENT_SECTION":
        is_req = "REQUIRED" in validators or True
        field_obj["consentSectionConfig"] = {
            "consents": [{"description": title, "required": is_req}],
            "required": is_req,
            "files": [],
        }

    return {
        "x": 0,
        "y": order - 1,
        "rows": 1,
        "cols": 12,
        "field": field_obj,
    }


def run_step07(
    document: dict[str, Any],
    structure: dict[str, Any],
    *,
    template: dict[str, Any] | None = None,
    llm_client: LLMClient | None = None,
) -> dict[str, Any]:
    """Populate Step 06 sections with COI fields, validators, and source metadata."""
    client = llm_client or LLMClient()
    form = deepcopy(structure)

    sections_by_tech: dict[str, dict[str, Any]] = {}
    first_main_section: dict[str, Any] | None = None
    for tab in form.get("tabs", []):
        if tab.get("type") != "MAIN":
            continue
        for q in tab.get("questions", []):
            sec_field = q.get("field", {})
            if sec_field.get("type") in COI_SECTION_TYPES:
                sections_by_tech[sec_field["techName"]] = sec_field
                if first_main_section is None:
                    first_main_section = sec_field

    tpl_context = template["title"] if template else "brak"
    prompt = (
        "Wyekstrahuj pola formularza zgodnie z komponentami i walidatorami COI "
        f"(wzorzec: {tpl_context}). Zgody zawsze jako CONSENT_SECTION. "
        "Każde pole musi zawierać source_text, page i element_id."
    )

    llm_output = client.generate_structured(
        prompt,
        FIELDS_SCHEMA,
        mock_response_fn=lambda _model: _build_mock_fields(document, template, form),
    )

    for raw_field in llm_output["fields"]:
        sec_key = slugify_tech_name(str(raw_field["section_tech_name"]))
        target_sec = sections_by_tech.get(sec_key) or first_main_section
        if target_sec is None:
            continue
        grid = target_sec.setdefault("repeatingSectionGrid", [])
        grid.append(_to_coi_question_field(raw_field, order=len(grid) + 1))

    # Drop empty optional sections on MAIN tabs (keep every tab with >= 1 field).
    for tab in form.get("tabs", []):
        if tab.get("type") != "MAIN":
            continue
        non_empty = [q for q in tab.get("questions", []) if q["field"].get("repeatingSectionGrid")]
        if non_empty:
            for idx, q in enumerate(non_empty):
                q["y"] = idx
                q["field"]["questionOrder"] = idx + 1
            tab["questions"] = non_empty

    return form
