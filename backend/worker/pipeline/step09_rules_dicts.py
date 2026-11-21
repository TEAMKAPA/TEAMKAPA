"""Step 09: Deterministic domain rules, COI dictionary mapping, and conflicts."""

from __future__ import annotations

import re
import unicodedata
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from backend.shared.validators.domain import VALIDATORS

# General COI rule: dictionaries may only be attached to selectable field types.
DICTIONARY_ALLOWED_TYPES: frozenset[str] = frozenset({"dropdown", "radio", "checkbox"})

# Official 6 COI dictionaries and their permitted field types.
COI_DICTIONARIES: dict[str, frozenset[str]] = {
    "Województwo": frozenset({"dropdown"}),
    "Powiat": frozenset({"dropdown"}),
    "Gmina": frozenset({"dropdown"}),
    "Miejscowość": frozenset({"dropdown"}),
    "Kraje": frozenset({"dropdown"}),
    "Numery kierunkowe": DICTIONARY_ALLOWED_TYPES,
}

_TYPE_ALIASES: dict[str, str] = {
    "select": "dropdown",
    "lista rozwijalna": "dropdown",
    "combobox": "dropdown",
    "radiobutton": "radio",
    "radio_button": "radio",
    "string": "text",
    "tel": "phone",
}

_COI_DICT_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "Numery kierunkowe",
        re.compile(
            r"\b(?:numery?\s+kierunkow\w*|nr\.?\s*kierunkow\w*|kierunkow\w*"
            r"|prefiks\w*\s+tel\w*|kod\s+kraju\s+tel\w*|dial\s*code|calling\s*code)\b"
        ),
    ),
    ("Województwo", re.compile(r"\bwojewodztw\w*\b")),
    ("Powiat", re.compile(r"\bpowiat\w*\b")),
    ("Gmina", re.compile(r"\bgmin\w*\b")),
    (
        "Miejscowość",
        re.compile(r"\b(?:miejscowosc\w*|miasto|miasta|miejsce\s+zamieszkania)\b"),
    ),
    (
        "Kraje",
        re.compile(r"\b(?:kraj\w*|panstw\w*|obywatelstw\w*|country|citizenship|nationality)\b"),
    ),
]


@dataclass(frozen=True)
class DomainRuleMatch:
    """Result of deterministic domain inference for a form field."""

    rule_name: str
    field_type: str
    validator: str | None
    max_length: int | None


_DOMAIN_LABEL_PATTERNS: list[tuple[str, re.Pattern[str], str, str, int]] = [
    ("pesel", re.compile(r"\bpesel\b"), "text", "pesel", 11),
    (
        "nip",
        re.compile(r"\b(?:nip|identyfikacji\s+podatkow\w*)\b"),
        "text",
        "nip",
        10,
    ),
    ("regon", re.compile(r"\bregon\b"), "text", "regon", 14),
    (
        "postal_code",
        re.compile(r"\b(?:kod\s+pocztow\w*|postal\s*code|zip\s*code)\b"),
        "text",
        "postal_code",
        6,
    ),
    (
        "email",
        re.compile(r"(?:\be-?mail\b|adres\s+mail\w*|poczt\w*\s+elektroniczn\w*)"),
        "email",
        "email",
        254,
    ),
    (
        "phone",
        re.compile(r"\b(?:telefon\w*|nr\.?\s*tel\.?|tel\.?|komorkow\w*|phone|mobile)\b"),
        "phone",
        "phone",
        15,
    ),
    (
        "date",
        re.compile(r"\b(?:dat[ayie]|date|rrrr-mm-dd|dd\.mm\.rrrr|dd-mm-rrrr)\b"),
        "date",
        "date",
        10,
    ),
]

_BOX_COUNT_RULES: dict[int, DomainRuleMatch] = {
    11: DomainRuleMatch("pesel", "text", "pesel", 11),
    10: DomainRuleMatch("nip", "text", "nip", 10),
    14: DomainRuleMatch("regon", "text", "regon", 14),
    5: DomainRuleMatch("postal_code", "text", "postal_code", 6),
    6: DomainRuleMatch("postal_code", "text", "postal_code", 6),
}


def _normalize_text(text: str) -> str:
    """Lowercase text and strip Polish diacritics for resilient matching."""
    replaced = text.lower().replace("ł", "l")
    decomposed = unicodedata.normalize("NFKD", replaced)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def _slugify(text: str) -> str:
    normalized = _normalize_text(text)
    slug = re.sub(r"[^a-z0-9]+", "_", normalized).strip("_")
    return slug or "value"


def normalize_field_type(field_type: str | None) -> str | None:
    """Normalize common field type aliases to canonical names."""
    if field_type is None:
        return None
    key = _normalize_text(str(field_type)).strip()
    return _TYPE_ALIASES.get(key, key)


def match_coi_dictionary(label: str) -> str | None:
    """Map a field label to one of the 6 official COI dictionaries, if applicable."""
    normalized = _normalize_text(label)
    for dict_name, pattern in _COI_DICT_PATTERNS:
        if pattern.search(normalized):
            return dict_name
    return None


def infer_domain_rule(label: str, box_count: int | None = None) -> DomainRuleMatch | None:
    """Infer field type, validator, and max_length from label semantics and boxes."""
    normalized = _normalize_text(label)

    for rule_name, pattern, field_type, validator, max_len in _DOMAIN_LABEL_PATTERNS:
        if pattern.search(normalized):
            if rule_name == "regon" and box_count in (9, 14):
                max_len = box_count
            return DomainRuleMatch(rule_name, field_type, validator, max_len)

    if box_count is not None and box_count in _BOX_COUNT_RULES:
        return _BOX_COUNT_RULES[box_count]

    return None


def _build_dictionary_proposal(
    field_id: str, label: str, field_type: str, options: list[str]
) -> dict[str, Any]:
    clean_options = [str(opt).strip() for opt in options if str(opt).strip()]
    return {
        "field_id": field_id,
        "name": label.strip() or field_id,
        "values": clean_options,
        "structure": {
            "key_type": "code",
            "label_type": "string",
            "entries": [{"code": _slugify(opt), "label": opt} for opt in clean_options],
        },
        "usage": (
            f"Pole '{label}' (typ: {field_type}) — zamknięta lista wartości "
            "spoza 6 oficjalnych słowników COI."
        ),
    }


def _apply_override(
    field: dict[str, Any],
    conflicts: list[dict[str, Any]],
    *,
    field_id: str,
    label: str,
    attribute: str,
    rule_value: Any,
    rule: str,
    reason: str,
) -> None:
    llm_value = field.get(attribute)
    if llm_value is not None and llm_value != rule_value:
        conflicts.append(
            {
                "field_id": field_id,
                "field_label": label,
                "attribute": attribute,
                "llm_value": llm_value,
                "rule_value": rule_value,
                "rule": rule,
                "reason": reason,
            }
        )
    field[attribute] = rule_value


def _apply_coi_dictionary(
    field: dict[str, Any],
    conflicts: list[dict[str, Any]],
    *,
    field_id: str,
    label: str,
    coi_dict: str,
) -> None:
    allowed_types = COI_DICTIONARIES[coi_dict]
    rule_tag = f"coi_dictionary:{coi_dict}"

    if field.get("type") not in allowed_types:
        _apply_override(
            field,
            conflicts,
            field_id=field_id,
            label=label,
            attribute="type",
            rule_value="dropdown",
            rule=rule_tag,
            reason=(f"Słownik COI '{coi_dict}' wymaga typu z zakresu {sorted(allowed_types)}."),
        )

    _apply_override(
        field,
        conflicts,
        field_id=field_id,
        label=label,
        attribute="dictionary",
        rule_value=coi_dict,
        rule=rule_tag,
        reason=f"Pole dopasowane deterministycznie do słownika COI '{coi_dict}'.",
    )

    if field.get("validator") is not None:
        _apply_override(
            field,
            conflicts,
            field_id=field_id,
            label=label,
            attribute="validator",
            rule_value=None,
            rule=rule_tag,
            reason=f"Pole słownikowe COI '{coi_dict}' nie używa walidatora tekstu.",
        )
    else:
        field["validator"] = None


def apply_rules_and_dicts(fields: list[dict[str, Any]]) -> dict[str, Any]:
    """Apply deterministic domain rules and COI dictionary mappings to form fields.

    Deterministic rules always take precedence over LLM suggestions; every
    overridden attribute is appended to the returned ``conflicts`` list.
    """
    enriched_fields: list[dict[str, Any]] = []
    dictionary_proposals: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []

    for index, raw_field in enumerate(fields):
        field = deepcopy(raw_field)
        label = str(field.get("label", "")).strip()
        field_id = str(field.get("id") or _slugify(label) or f"field_{index + 1}")
        field["id"] = field_id

        if "type" in field:
            field["type"] = normalize_field_type(field.get("type"))

        box_count_raw = field.get("box_count")
        box_count = None
        if isinstance(box_count_raw, int) and box_count_raw > 0:
            box_count = box_count_raw
        options = field.get("options") or []
        llm_dict = field.get("dictionary")

        # 1. Deterministic COI dictionary match from field label takes top priority.
        coi_dict = match_coi_dictionary(label)
        if coi_dict is not None:
            _apply_coi_dictionary(
                field,
                conflicts,
                field_id=field_id,
                label=label,
                coi_dict=coi_dict,
            )
            enriched_fields.append(field)
            continue

        # 2. Deterministic domain rule (PESEL, NIP, REGON, postal code, phone, etc.).
        domain_match = infer_domain_rule(label, box_count=box_count)
        if domain_match is not None:
            rule_tag = f"domain:{domain_match.rule_name}"
            _apply_override(
                field,
                conflicts,
                field_id=field_id,
                label=label,
                attribute="type",
                rule_value=domain_match.field_type,
                rule=rule_tag,
                reason=(
                    f"Reguła dziedzinowa '{domain_match.rule_name}' wymusza typ "
                    f"'{domain_match.field_type}'."
                ),
            )
            if domain_match.validator in VALIDATORS:
                _apply_override(
                    field,
                    conflicts,
                    field_id=field_id,
                    label=label,
                    attribute="validator",
                    rule_value=domain_match.validator,
                    rule=rule_tag,
                    reason=(f"Reguła dziedzinowa przypisuje walidator '{domain_match.validator}'."),
                )
            if domain_match.max_length is not None:
                _apply_override(
                    field,
                    conflicts,
                    field_id=field_id,
                    label=label,
                    attribute="max_length",
                    rule_value=domain_match.max_length,
                    rule=rule_tag,
                    reason=(f"Reguła dziedzinowa określa max_length={domain_match.max_length}."),
                )
            if llm_dict is not None:
                _apply_override(
                    field,
                    conflicts,
                    field_id=field_id,
                    label=label,
                    attribute="dictionary",
                    rule_value=None,
                    rule=rule_tag,
                    reason=(
                        f"Pole z regułą '{domain_match.rule_name}' nie może mieć "
                        "przypisanego słownika."
                    ),
                )
            else:
                field["dictionary"] = None
            enriched_fields.append(field)
            continue

        # 3. If LLM attached an official COI dictionary, enforce COI type rules on it.
        if llm_dict in COI_DICTIONARIES:
            _apply_coi_dictionary(
                field,
                conflicts,
                field_id=field_id,
                label=label,
                coi_dict=llm_dict,
            )
            enriched_fields.append(field)
            continue

        # 4. Reject non-COI dictionary names suggested by LLM.
        if llm_dict is not None:
            _apply_override(
                field,
                conflicts,
                field_id=field_id,
                label=label,
                attribute="dictionary",
                rule_value=None,
                rule="coi_dictionary_whitelist",
                reason=(f"Słownik '{llm_dict}' nie należy do 6 oficjalnych słowników COI."),
            )

        # 5. Fallback: infer max_length from box_count if not set.
        if box_count is not None and field.get("max_length") is None:
            field["max_length"] = box_count

        # 6. Closed-list fields outside the 6 COI dicts yield a new dictionary proposal.
        if isinstance(options, list) and len(options) >= 2:
            if field.get("type") not in DICTIONARY_ALLOWED_TYPES:
                _apply_override(
                    field,
                    conflicts,
                    field_id=field_id,
                    label=label,
                    attribute="type",
                    rule_value="dropdown",
                    rule="coi_closed_list",
                    reason=(
                        "Pole z zamkniętą listą wartości wymaga typu dropdown, radio lub checkbox."
                    ),
                )
            dictionary_proposals.append(
                _build_dictionary_proposal(
                    field_id=field_id,
                    label=label,
                    field_type=field["type"],
                    options=options,
                )
            )

        if field.get("type") is None:
            field["type"] = "text"
        field.setdefault("validator", None)
        field.setdefault("dictionary", None)
        enriched_fields.append(field)

    return {
        "fields": enriched_fields,
        "dictionary_proposals": dictionary_proposals,
        "conflicts": conflicts,
    }


_COI_SELECTABLE_TYPES: frozenset[str] = frozenset({"SELECT", "RADIO", "CHECKBOX"})
_COI_SKIP_TYPES: frozenset[str] = frozenset(
    {"CONSENT_SECTION", "INFO_SECTION", "TEXT_BLOCK", "FILE_UPLOADER"}
)
_COI_DOMAIN_VALIDATORS: frozenset[str] = frozenset({"PESEL", "NIP", "REGON", "ZIP_CODE", "EMAIL"})
_DOMAIN_TO_COI_TYPE: dict[str, str] = {
    "text": "TEXT_FIELD",
    "phone": "TEXT_FIELD",
    "email": "TEXT_FIELD",
    "date": "DATEPICKER",
    "number": "NUMBER",
    "textarea": "TEXTAREA",
    "dropdown": "SELECT",
    "radio": "RADIO",
    "checkbox": "CHECKBOX",
}
_DOMAIN_TO_COI_VALIDATOR: dict[str, str] = {
    "pesel": "PESEL",
    "nip": "NIP",
    "regon": "REGON",
    "postal_code": "ZIP_CODE",
    "email": "EMAIL",
}


def _extract_option_labels(options: Any) -> list[str]:
    if not isinstance(options, list):
        return []
    labels: list[str] = []
    for opt in options:
        if isinstance(opt, dict):
            name = str(opt.get("name") or opt.get("label") or "").strip()
            if name:
                labels.append(name)
        elif str(opt).strip():
            labels.append(str(opt).strip())
    return labels


def _apply_coi_dict_to_leaf(
    field: dict[str, Any],
    conflicts: list[dict[str, Any]],
    *,
    field_id: str,
    label: str,
    coi_dict: str,
) -> None:
    allowed = (
        frozenset({"SELECT"})
        if COI_DICTIONARIES[coi_dict] == frozenset({"dropdown"})
        else _COI_SELECTABLE_TYPES
    )
    rule_tag = f"coi_dictionary:{coi_dict}"

    if field.get("type") not in allowed:
        _apply_override(
            field,
            conflicts,
            field_id=field_id,
            label=label,
            attribute="type",
            rule_value="SELECT",
            rule=rule_tag,
            reason=(f"Słownik COI '{coi_dict}' wymaga typu z zakresu {sorted(allowed)}."),
        )

    _apply_override(
        field,
        conflicts,
        field_id=field_id,
        label=label,
        attribute="dictionaryName",
        rule_value=coi_dict,
        rule=rule_tag,
        reason=f"Pole dopasowane deterministycznie do słownika COI '{coi_dict}'.",
    )
    field["options"] = []


def apply_rules_to_coi_form(form_data: dict[str, Any]) -> dict[str, Any]:
    """Apply Step 09 rules and COI dictionaries directly to a COI FormDefinition."""
    result = deepcopy(form_data)
    dictionary_proposals: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []

    for tab in result.get("tabs", []):
        if tab.get("type") != "MAIN":
            continue
        for sec_q in tab.get("questions", []):
            sec_field = sec_q.get("field", {})
            for child in sec_field.get("repeatingSectionGrid", []):
                field = child.get("field", {})
                if field.get("type") in _COI_SKIP_TYPES:
                    continue

                label = str(field.get("title") or field.get("techName") or "").strip()
                field_id = str(field.get("techName") or field.get("id") or _slugify(label))
                box_raw = field.get("box_count")
                box_count = box_raw if isinstance(box_raw, int) and box_raw > 0 else None
                llm_dict = field.get("dictionaryName")
                option_labels = _extract_option_labels(field.get("options"))

                # 1. Official COI dictionary match from label.
                coi_dict = match_coi_dictionary(label)
                if coi_dict is not None:
                    _apply_coi_dict_to_leaf(
                        field,
                        conflicts,
                        field_id=field_id,
                        label=label,
                        coi_dict=coi_dict,
                    )
                    continue

                # 2. Deterministic domain rule (PESEL, NIP, REGON, ZIP_CODE, etc.).
                domain_match = infer_domain_rule(label, box_count=box_count)
                if domain_match is not None:
                    rule_tag = f"domain:{domain_match.rule_name}"
                    coi_type = _DOMAIN_TO_COI_TYPE.get(domain_match.field_type, "TEXT_FIELD")
                    _apply_override(
                        field,
                        conflicts,
                        field_id=field_id,
                        label=label,
                        attribute="type",
                        rule_value=coi_type,
                        rule=rule_tag,
                        reason=(
                            f"Reguła dziedzinowa '{domain_match.rule_name}' "
                            f"wymusza typ COI '{coi_type}'."
                        ),
                    )
                    coi_val = _DOMAIN_TO_COI_VALIDATOR.get(domain_match.validator or "")
                    if coi_val is not None:
                        old_vals = list(field.get("validation") or [])
                        wrong_domain = [
                            v for v in old_vals if v in _COI_DOMAIN_VALIDATORS and v != coi_val
                        ]
                        new_vals = [v for v in old_vals if v not in wrong_domain]
                        if coi_val not in new_vals:
                            new_vals.append(coi_val)
                        if wrong_domain:
                            conflicts.append(
                                {
                                    "field_id": field_id,
                                    "field_label": label,
                                    "attribute": "validation",
                                    "llm_value": old_vals,
                                    "rule_value": new_vals,
                                    "rule": rule_tag,
                                    "reason": (
                                        f"Reguła dziedzinowa nadpisuje walidator na '{coi_val}'."
                                    ),
                                }
                            )
                        field["validation"] = new_vals

                    if llm_dict is not None:
                        _apply_override(
                            field,
                            conflicts,
                            field_id=field_id,
                            label=label,
                            attribute="dictionaryName",
                            rule_value=None,
                            rule=rule_tag,
                            reason=(
                                f"Pole z regułą '{domain_match.rule_name}' nie może "
                                "mieć przypisanego słownika."
                            ),
                        )
                        field.pop("dictionaryName", None)
                    continue

                # 3. Official COI dictionary explicitly set by LLM -> enforce type rule.
                if llm_dict in COI_DICTIONARIES:
                    _apply_coi_dict_to_leaf(
                        field,
                        conflicts,
                        field_id=field_id,
                        label=label,
                        coi_dict=llm_dict,
                    )
                    continue

                # 4. Reject non-COI dictionaryName.
                if llm_dict is not None:
                    _apply_override(
                        field,
                        conflicts,
                        field_id=field_id,
                        label=label,
                        attribute="dictionaryName",
                        rule_value=None,
                        rule="coi_dictionary_whitelist",
                        reason=(f"Słownik '{llm_dict}' nie należy do 6 oficjalnych słowników COI."),
                    )
                    field.pop("dictionaryName", None)

                # 5. Closed-list field outside 6 COI dicts -> dictionary proposal.
                if len(option_labels) >= 2:
                    if field.get("type") not in _COI_SELECTABLE_TYPES:
                        _apply_override(
                            field,
                            conflicts,
                            field_id=field_id,
                            label=label,
                            attribute="type",
                            rule_value="SELECT",
                            rule="coi_closed_list",
                            reason=(
                                "Pole z zamkniętą listą wartości wymaga typu "
                                "SELECT, RADIO lub CHECKBOX."
                            ),
                        )
                    dictionary_proposals.append(
                        _build_dictionary_proposal(
                            field_id=field_id,
                            label=label,
                            field_type=str(field.get("type", "SELECT")),
                            options=option_labels,
                        )
                    )

    result["dictionary_proposals"] = dictionary_proposals
    result["conflicts"] = conflicts
    return result


def run_step09(form_data: dict[str, Any]) -> dict[str, Any]:
    """Run Step 09 on either a COI FormDefinition (``tabs``) or flat ``fields``."""
    if "tabs" in form_data:
        return apply_rules_to_coi_form(form_data)

    result = deepcopy(form_data)
    step_output = apply_rules_and_dicts(form_data.get("fields", []))
    result["fields"] = step_output["fields"]
    result["dictionary_proposals"] = step_output["dictionary_proposals"]
    result["conflicts"] = step_output["conflicts"]
    return result
