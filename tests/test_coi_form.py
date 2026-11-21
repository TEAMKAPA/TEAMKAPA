"""Modele FormDefinition: zgodność z oficjalnym schematem COI i reguły wyrażone typami."""

import json
from typing import Any

import jsonschema
import pytest
from pydantic import ValidationError

from backend.shared.schemas.coi_form import FieldType, FormDefinition
from backend.shared.schemas.quality import Severity
from backend.shared.validators.form_rules import validate_form_rules
from tests.coi_helpers import leaf, official_schema, reference_forms, sample, section


def _parse(data: dict[str, Any]) -> FormDefinition:
    return FormDefinition.model_validate(data)


def _all_keys(node: Any) -> set[str]:
    if isinstance(node, dict):
        return set(node) | {k for v in node.values() for k in _all_keys(v)}
    if isinstance(node, list):
        return {k for v in node for k in _all_keys(v)}
    return set()


# ------------------------------------------------------------- pliki referencyjne COI


@pytest.mark.parametrize("name", sorted(reference_forms()))
def test_official_reference_forms_are_accepted(name: str) -> None:
    """Konfiguracje wyeksportowane z COI (materiał organizatora) przechodzą model i reguły."""
    data = reference_forms()[name]
    form = _parse(data)
    assert not [i for i in validate_form_rules(form) if i.severity is Severity.ERROR]
    jsonschema.Draft202012Validator(official_schema()).validate(form.to_coi_json())


@pytest.mark.parametrize("name", sorted(reference_forms()))
def test_official_reference_round_trip_keeps_shape(name: str) -> None:
    """Wynik ma te same klucze w tej samej kolejności co eksport COI.

    Jedyne dopuszczalne różnice: ``validation: {}`` → ``[]``, brakujące ``states`` → ``[]``
    i końcowe białe znaki w zapytaniach JSONata.
    """
    original = reference_forms()[name]
    dumped = _parse(original).to_coi_json()

    def compare(a: Any, b: Any, path: str) -> None:
        if isinstance(b, dict):
            assert isinstance(a, dict), path
            assert [k for k in a if k in b] == list(b), path
            assert set(a) - set(b) <= {"states"}, path
            for key, value in b.items():
                if key == "validation" and value == {}:
                    assert a[key] == [], path
                elif key == "query":
                    assert a[key] == value.strip(), path
                else:
                    compare(a[key], value, f"{path}.{key}")
        elif isinstance(b, list):
            assert isinstance(a, list) and len(a) == len(b), path
            for i, (x, y) in enumerate(zip(a, b, strict=True)):
                compare(x, y, f"{path}[{i}]")
        else:
            assert a == b, path

    compare(dumped, original, "$")


def test_coi_conventions_option_id_and_flow_link_id() -> None:
    data = sample()
    for option in leaf(data, 0, 1, 0)["options"]:
        del option["id"]
    data["flow"]["links"][0]["id"] = "a16ec"  # krótki identyfikator z edytora COI
    form = _parse(data).to_coi_json()
    assert all(o["id"] == o["name"] for o in leaf(form, 0, 1, 0)["options"])
    assert form["flow"]["links"][0]["id"] == "a16ec"


# ------------------------------------------------------------- zgodność ze schematem COI


def test_sample_round_trip_is_lossless() -> None:
    data = sample()
    assert _parse(data).to_coi_json() == data


def test_output_validates_against_official_schema() -> None:
    jsonschema.Draft202012Validator(official_schema()).validate(_parse(sample()).to_coi_json())


def test_section_children_key_uses_coi_spelling() -> None:
    """Pliki COI mają ``repeatingSectionGrid``; literówka ze schematu jest tylko czytana."""
    dumped = json.dumps(_parse(sample()).to_coi_json())
    assert '"repeatingSectionGrid"' in dumped
    assert '"repetingSectionGrid"' not in dumped

    legacy = json.loads(
        json.dumps(sample()).replace('"repeatingSectionGrid"', '"repetingSectionGrid"')
    )
    assert _parse(legacy).to_coi_json() == sample()


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
    reference_keys = {k for data in reference_forms().values() for k in _all_keys(data)}

    assert _all_keys(_parse(sample()).to_coi_json()) <= schema_keys | rule_keys | reference_keys


def test_structured_output_schema_is_generated_and_not_recursive() -> None:
    schema = FormDefinition.model_json_schema(by_alias=True)
    defs = schema["$defs"]
    assert "repeatingSectionGrid" in defs["SectionField"]["properties"]
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
    nested = section(data, 1, 0) | {"repeatingSectionGrid": []}
    section(data, 0, 0)["repeatingSectionGrid"].append(
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


def test_default_value_is_text_or_list_like_coi() -> None:
    data = sample()
    leaf(data, 1, 0, 0)["defaultValue"] = ["Polska"]  # SELECT ze słownikiem „Kraje” w COI
    assert _parse(data).to_coi_json() == data
    leaf(data, 1, 0, 0)["defaultValue"] = {"kraj": "Polska"}
    _expect_invalid(data)


@pytest.mark.parametrize(
    ("tab", "sec", "idx", "key", "value"),
    [
        (0, 0, 0, "options", [{"name": "A", "techName": "a"}]),  # TEXT_FIELD bez opcji
        (0, 0, 0, "repeatingSectionGrid", [{"x": 0, "y": 0, "rows": 1, "cols": 3}]),
        (3, 0, 0, "validation", ["REQUIRED"]),  # pole informacyjne bez walidatorów
    ],
)
def test_common_keys_hold_only_values_allowed_for_type(
    tab: int, sec: int, idx: int, key: str, value: Any
) -> None:
    data = sample()
    leaf(data, tab, sec, idx)[key] = value
    _expect_invalid(data)


def test_section_cannot_have_validators() -> None:
    data = sample()
    section(data, 0, 0)["validation"] = ["REQUIRED"]
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
