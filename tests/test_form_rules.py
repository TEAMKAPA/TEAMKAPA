"""Reguły semantyczne formularza (backend.shared.validators.form_rules)."""

from typing import Any

import pytest

from backend.shared.schemas.coi_form import FormDefinition
from backend.shared.schemas.initial_data import INITIAL_DATA_CATALOG, InitialDataSource
from backend.shared.schemas.quality import Severity
from backend.shared.validators.form_rules import (
    StateReference,
    extract_references,
    has_errors,
    validate_form_rules,
)
from tests.coi_helpers import leaf, rule_codes, sample, section

NEW_ID = "6f1c0b6e-0000-4000-8000-0000000000{:02d}"


def test_sample_has_no_issues() -> None:
    assert validate_form_rules(FormDefinition.model_validate(sample())) == []


# ------------------------------------------------------------------ JSONata


def test_extracts_official_example_queries() -> None:
    section_example = (
        "\"Opcja 1\" in tabs['Strona 1' in title].questions['sekcja_1' in field.techName]"
        ".field.repeatingSectionGrid['pole_1' in field.techName].field.__userAnswer"
    )
    repeating_example = (
        "\"Opcja 1\" in tabs['Strona 1'in title].questions['sekcja_1' in field.techName]"
        ".field.__userAnswer[$repeatingSectionIndex]['pole_1'in fieldTechName].answers"
    )
    expected = [StateReference("Strona 1", "sekcja_1", "pole_1", "Opcja 1")]
    assert extract_references(section_example) == expected
    assert extract_references(repeating_example) == expected


def test_extracts_schema_spelling_and_parenthesised_query() -> None:
    query = (
        "(\"Polska\" in tabs['Dane' in title].questions['adres' in field.techName]"
        ".field.repetingSectionGrid['kraj' in field.techName].field.__userAnswer)"
    )
    assert extract_references(query) == [StateReference("Dane", "adres", "kraj", "Polska")]


def _set_query(data: dict[str, Any], query: str) -> None:
    section(data, 0, 3)["states"][0]["query"] = query


def _ref(tab: str, sec: str, field: str, value: str = "Innej osoby") -> str:
    return (
        f"\"{value}\" in tabs['{tab}' in title].questions['{sec}' in field.techName]"
        f".field.repeatingSectionGrid['{field}' in field.techName].field.__userAnswer"
    )


@pytest.mark.parametrize(
    ("query", "code"),
    [
        (_ref("Nie ma", "podmiot_wniosku", "w_czyim_imieniu"), "STATE_UNKNOWN_TAB"),
        (_ref("Dane wnioskodawcy", "brak", "w_czyim_imieniu"), "STATE_UNKNOWN_SECTION"),
        (_ref("Dane wnioskodawcy", "podmiot_wniosku", "brak"), "STATE_UNKNOWN_FIELD"),
        (_ref("Dane wnioskodawcy", "podmiot_wniosku", "w_czyim_imieniu", "Sąsiada"),
         "STATE_UNKNOWN_OPTION"),
        (_ref("Dane wnioskodawcy", "dane_innej_osoby", "inna_imie"), "STATE_SELF_REFERENCE"),
        (_ref("Dane do kontaktu", "dane_kontaktowe", "telefon"), "STATE_FORWARD_REFERENCE"),
        ("$exists(cokolwiek)", "STATE_QUERY_UNPARSED"),
    ],
)  # fmt: skip
def test_state_reference_problems(query: str, code: str) -> None:
    data = sample()
    _set_query(data, query)
    assert code in rule_codes(data)


def test_state_cycle_detected() -> None:
    data = sample()
    # jako_kto zależy od w_czyim_imieniu; dodajemy zależność odwrotną.
    leaf(data, 0, 1, 0)["states"] = [
        {
            "type": "SHOW",
            "query": _ref("Dane wnioskodawcy", "podmiot_wniosku", "jako_kto", "Pełnomocnik"),
        }
    ]
    assert "STATE_CYCLE" in rule_codes(data)


def test_dictionary_field_values_are_not_checked_against_options() -> None:
    data = sample()
    leaf(data, 1, 0, 1)["states"] = [
        {"type": "SHOW", "query": _ref("Lokalizacja budynku", "lokalizacja", "kraj", "Polska")}
    ]
    assert rule_codes(data) == set()


# ------------------------------------------------------------------ strony i przepływ


def test_single_tab_rejected() -> None:
    data = sample()
    data["tabs"] = data["tabs"][-1:]
    data["flow"]["links"] = []
    assert "TABS_MIN_TWO" in rule_codes(data)


def test_last_tab_must_be_finish() -> None:
    data = sample()
    data["tabs"][-1]["type"] = "MAIN"
    codes = rule_codes(data)
    assert "FINISH_TAB_LAST" in codes


def test_only_one_finish() -> None:
    data = sample()
    data["tabs"][1]["type"] = "FINISH"
    assert {"FINISH_TAB_SINGLE", "FINISH_ONLY_INFORMATIONAL"} <= rule_codes(data)


def test_finish_only_informational_fields() -> None:
    data = sample()
    grid = section(data, 3, 0)["repeatingSectionGrid"]
    grid.append({"x": 0, "y": 1, "rows": 1, "cols": 3, "field": leaf(data, 1, 0, 1)})
    assert "FINISH_ONLY_INFORMATIONAL" in rule_codes(data)


def test_finish_with_text_block_like_coi() -> None:
    """Strona końcowa w plikach COI: sekcja „Podgląd uzupełnionego wniosku” z TEXT_BLOCK."""
    data = sample()
    info = leaf(data, 3, 0, 0)
    info["type"] = "TEXT_BLOCK"
    assert rule_codes(data) == set()


def test_tab_without_fields() -> None:
    data = sample()
    section(data, 1, 0)["repeatingSectionGrid"] = []
    assert {"TAB_EMPTY", "SECTION_EMPTY"} <= rule_codes(data)


def test_duplicate_tab_title() -> None:
    data = sample()
    data["tabs"][2]["title"] = data["tabs"][1]["title"]
    assert "TAB_TITLE_DUPLICATE" in rule_codes(data)


def test_duplicate_ids() -> None:
    data = sample()
    leaf(data, 1, 0, 2)["id"] = leaf(data, 1, 0, 1)["id"]
    assert "DUPLICATE_ID" in rule_codes(data)


def test_duplicate_tech_name_in_section() -> None:
    data = sample()
    leaf(data, 1, 0, 2)["techName"] = leaf(data, 1, 0, 1)["techName"]
    assert "TECHNAME_DUPLICATE_IN_SECTION" in rule_codes(data)


def test_duplicate_option_name() -> None:
    data = sample()
    options = leaf(data, 0, 4, 0)["options"]
    options[1]["name"] = options[0]["name"]
    assert "OPTION_NAME_DUPLICATE" in rule_codes(data)


def test_flow_unknown_tab_and_unreachable() -> None:
    data = sample()
    data["flow"]["links"][1]["target"] = NEW_ID.format(1)
    codes = rule_codes(data)
    assert {"FLOW_UNKNOWN_TAB", "FLOW_UNREACHABLE_TAB", "FLOW_NO_PATH_TO_FINISH"} <= codes


def test_flow_self_link_and_finish_outgoing() -> None:
    data = sample()
    finish_id = data["tabs"][-1]["id"]
    first_id = data["tabs"][0]["id"]
    data["flow"]["links"] += [
        {"id": NEW_ID.format(2), "source": first_id, "target": first_id},
        {"id": NEW_ID.format(3), "source": finish_id, "target": first_id},
    ]
    assert {"FLOW_SELF_LINK", "FLOW_FINISH_OUTGOING"} <= rule_codes(data)


def test_flow_missing_links() -> None:
    data = sample()
    data["flow"]["links"] = []
    assert {"FLOW_UNREACHABLE_TAB", "FLOW_NO_PATH_TO_FINISH"} <= rule_codes(data)


# ------------------------------------------------------------------ pola


def test_initial_data_unknown_attribute() -> None:
    data = sample()
    leaf(data, 0, 2, 0)["defaultValue"] = "MLPZ.firstName"
    assert "INITIAL_DATA_UNKNOWN" in rule_codes(data)


def test_initial_data_must_be_read_only() -> None:
    data = sample()
    leaf(data, 0, 2, 0)["readOnly"] = False
    assert "INITIAL_DATA_NOT_READONLY" in rule_codes(data)


@pytest.mark.parametrize("reference", ["MLPZ.email", "MLPZ.phoneNumber", "MLPZ.postCode"])
def test_contact_and_address_initial_data_may_stay_editable(reference: str) -> None:
    """W obu referencyjnych formularzach COI adres i kontakt z MLPZ mają readOnly: false."""
    data = sample()
    field = leaf(data, 2, 0, 0)
    field.update(defaultValue=reference, defaultValueType="EXTERNAL_SYSTEM", readOnly=False)
    assert "INITIAL_DATA_NOT_READONLY" not in rule_codes(data)


@pytest.mark.parametrize("reference", ["MLPZ.name", "MLPZ.surname", "PESEL.pesel"])
def test_identity_initial_data_must_be_read_only(reference: str) -> None:
    data = sample()
    field = leaf(data, 2, 0, 0)
    field.update(defaultValue=reference, defaultValueType="EXTERNAL_SYSTEM", readOnly=False)
    assert "INITIAL_DATA_NOT_READONLY" in rule_codes(data)


def test_initial_data_type_mismatch() -> None:
    data = sample()
    leaf(data, 0, 2, 0)["defaultValue"] = "PESEL.birthDate"
    assert "INITIAL_DATA_TYPE_MISMATCH" in rule_codes(data)


def test_initial_data_reference_without_type() -> None:
    data = sample()
    field = leaf(data, 0, 2, 0)
    del field["defaultValueType"]
    assert "INITIAL_DATA_MISSING_TYPE" in rule_codes(data)


def test_initial_data_catalog_matches_material() -> None:
    counts = {s: 0 for s in InitialDataSource}
    for entry in INITIAL_DATA_CATALOG.values():
        counts[entry.source] += 1
    assert counts == {
        InitialDataSource.PESEL: 16,
        InitialDataSource.PESEL_ADDRESS: 18,
        InitialDataSource.MLPZ: 17,
    }
    assert INITIAL_DATA_CATALOG["PESEL.name"].reference == "PESEL.name"


def test_input_without_title_is_warning() -> None:
    data = sample()
    del leaf(data, 1, 0, 1)["title"]
    issues = validate_form_rules(FormDefinition.model_validate(data))
    assert [(i.code, i.severity) for i in issues] == [("INPUT_WITHOUT_TITLE", Severity.WARNING)]
    assert not has_errors(issues)


def test_grid_overlap_is_warning() -> None:
    data = sample()
    section(data, 1, 0)["repeatingSectionGrid"][1]["y"] = 0
    assert "GRID_OVERLAP" in rule_codes(data)


def test_issue_paths_point_into_coi_json() -> None:
    data = sample()
    leaf(data, 0, 2, 0)["readOnly"] = False
    issue = next(
        i
        for i in validate_form_rules(FormDefinition.model_validate(data))
        if i.code == "INITIAL_DATA_NOT_READONLY"
    )
    assert issue.path == "/tabs/0/questions/2/field/repeatingSectionGrid/0/field/readOnly"
