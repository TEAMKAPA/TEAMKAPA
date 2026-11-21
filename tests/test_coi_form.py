"""Modele FormDefinition: zgodność z oficjalnym schematem COI i reguły wyrażone typami."""

import json
from typing import Any

import jsonschema
import pytest
from pydantic import ValidationError

from backend.shared.schemas.coi_form import FieldType, FormDefinition
from tests.coi_helpers import leaf, official_schema, sample, section


def _parse(data: dict[str, Any]) -> FormDefinition:
    return FormDefinition.model_validate(data)


# ------------------------------------------------------------- zgodność ze schematem COI


def test_sample_round_trip_is_lossless() -> None:
    data = sample()
    assert _parse(data).to_coi_json() == data


def test_output_validates_against_official_schema() -> None:
    jsonschema.Draft202012Validator(official_schema()).validate(_parse(sample()).to_coi_json())


def test_section_children_key_uses_schema_spelling() -> None:
    dumped = json.dumps(_parse(sample()).to_coi_json())
    assert '"repetingSectionGrid"' in dumped
    assert '"repeatingSectionGrid"' not in dumped


def test_every_output_key_exists_in_official_schema_or_rules() -> None:
    """Pilnuje, żeby model nie emitował kluczy spoza schematu i reguł technicznych."""
    schema_keys: set[str] = set()

    def collect(node: Any) -> None:
        if isinstance(node, dict):
            schema_keys.update(node.get("properties", {}).keys())
            for value in node.values():
                collect(value)
        elif isinstance(node, list):
            for value in node:
                collect(value)

    collect(official_schema())
    rule_keys = {"searchEnabled", "selectAllEnabled", "required"}

    def keys(node: Any) -> set[str]:
        if isinstance(node, dict):
            return set(node) | {k for v in node.values() for k in keys(v)}
        if isinstance(node, list):
            return {k for v in node for k in keys(v)}
        return set()

    assert keys(_parse(sample()).to_coi_json()) <= schema_keys | rule_keys


def test_structured_output_schema_is_generated_and_not_recursive() -> None:
    schema = FormDefinition.model_json_schema(by_alias=True)
    defs = schema["$defs"]
    assert "repetingSectionGrid" in defs["SectionField"]["properties"]
    # Pola w sekcji to wyłącznie liście — schemat nie odwołuje się z powrotem do sekcji,
    # więc nadaje się do structured output (vLLM) bez rekurencji.
    field_question = json.dumps(defs["FieldQuestion"])
    assert '#/$defs/SectionField"' not in field_question
    assert '#/$defs/SectionQuestion"' not in field_question


def test_unknown_values_are_rejected_by_enums() -> None:
    schema = FormDefinition.model_json_schema(by_alias=True)
    assert schema["$defs"]["TabType"]["enum"] == ["MAIN", "FINISH"]
    assert set(schema["$defs"]["Validator"]["enum"]) == {
        "REQUIRED", "EMAIL", "PESEL", "NIP", "REGON", "ZIP_CODE", "EPUAP", "ADE",
        "SERIAL_NUMBER_ID_CARD",
    }  # fmt: skip


# ------------------------------------------------------------- reguły strukturalne


def _expect_invalid(data: dict[str, Any], fragment: str = "") -> None:
    with pytest.raises(ValidationError) as exc:
        _parse(data)
    assert fragment in str(exc.value)


def test_section_cannot_be_nested_in_section() -> None:
    data = sample()
    nested = section(data, 1, 0) | {"repetingSectionGrid": []}
    section(data, 0, 0)["repetingSectionGrid"].append(
        {"x": 0, "y": 9, "rows": 1, "cols": 1, "field": nested}
    )
    _expect_invalid(data)


def test_field_must_be_inside_section() -> None:
    data = sample()
    data["tabs"][1]["questions"].append(
        {"x": 0, "y": 9, "rows": 1, "cols": 1, "field": leaf(data, 1, 0, 1)}
    )
    _expect_invalid(data)


def test_unknown_key_is_rejected() -> None:
    data = sample()
    leaf(data, 1, 0, 1)["placeholder"] = "np. Warszawa"
    _expect_invalid(data, "placeholder")


def test_input_field_must_declare_obligatoriness() -> None:
    data = sample()
    del leaf(data, 1, 0, 1)["validation"]
    _expect_invalid(data, "validation")


def test_text_field_accepts_domain_validators() -> None:
    data = sample()
    leaf(data, 1, 0, 1)["validation"] = ["REQUIRED", "NIP", "REGON", "EPUAP", "ADE"]
    _parse(data)


@pytest.mark.parametrize(("tab", "sec", "idx"), [(0, 1, 0), (1, 0, 0), (1, 0, 4), (1, 0, 5)])
def test_non_text_fields_accept_only_required_validator(tab: int, sec: int, idx: int) -> None:
    data = sample()
    leaf(data, tab, sec, idx)["validation"] = ["REQUIRED", "PESEL"]
    _expect_invalid(data)


def test_unknown_validator_rejected() -> None:
    data = sample()
    leaf(data, 1, 0, 1)["validation"] = ["PHONE"]
    _expect_invalid(data)


def test_choice_with_dictionary_must_have_empty_options() -> None:
    data = sample()
    leaf(data, 1, 0, 0)["options"] = [{"id": "a", "name": "Polska", "techName": "polska"}]
    _expect_invalid(data, "pustą tablicę options")


def test_choice_needs_options_or_dictionary() -> None:
    data = sample()
    leaf(data, 0, 1, 0)["options"] = []
    _expect_invalid(data, "wymaga opcji albo słownika")


def test_info_section_requires_description() -> None:
    data = sample()
    del leaf(data, 3, 0, 0)["description"]
    _expect_invalid(data, "description")


def test_section_state_only_show() -> None:
    data = sample()
    section(data, 0, 3)["states"][0]["type"] = "READ_ONLY"
    _expect_invalid(data)


def test_field_state_read_only_allowed() -> None:
    data = sample()
    leaf(data, 0, 1, 1)["states"][0]["type"] = "READ_ONLY"
    _parse(data)


def test_text_block_state_only_show() -> None:
    data = sample()
    leaf(data, 1, 0, 1).clear()
    leaf(data, 1, 0, 1).update(
        {
            "id": "6f1c0b6e-0000-4000-8000-000000000001",
            "type": "TEXT_BLOCK",
            "techName": "info",
            "description": "Tekst",
            "states": [{"type": "READ_ONLY", "query": "true"}],
        }
    )
    _expect_invalid(data)


def test_external_system_requires_default_value() -> None:
    data = sample()
    del leaf(data, 0, 2, 0)["defaultValue"]
    _expect_invalid(data, "EXTERNAL_SYSTEM")


def test_default_value_must_be_string_like_schema() -> None:
    data = sample()
    leaf(data, 1, 0, 0)["defaultValue"] = []
    _expect_invalid(data)


@pytest.mark.parametrize("tech_name", ["pole 1", "pole'1", '"pole"', "1pole", ""])
def test_tech_name_safe_for_jsonata(tech_name: str) -> None:
    data = sample()
    leaf(data, 1, 0, 1)["techName"] = tech_name
    _expect_invalid(data)


def test_form_title_max_255() -> None:
    data = sample()
    data["title"] = "W" * 256
    _expect_invalid(data)


def test_tab_type_must_be_uppercase_enum() -> None:
    data = sample()
    data["tabs"][-1]["type"] = "finish"
    _expect_invalid(data)


def test_consent_section_requires_consents() -> None:
    data = sample()
    leaf(data, 2, 1, 0)["consentSectionConfig"]["consents"] = []
    _expect_invalid(data)


def test_ids_must_be_uuid() -> None:
    data = sample()
    data["tabs"][0]["id"] = "strona-1"
    _expect_invalid(data)


def test_iter_fields_lists_every_leaf() -> None:
    form = _parse(sample())
    leaves = [f for _, _, f in form.iter_fields() if f is not None]
    assert len(leaves) == 21
    assert {f.type for f in leaves} >= {FieldType.RADIO, FieldType.INFO_SECTION}
