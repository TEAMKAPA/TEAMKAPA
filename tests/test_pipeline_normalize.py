"""Normalizacja wyniku etapów 05–09 do pliku importu COI."""

from typing import Any

import pytest

from backend.shared.schemas.coi_form import FieldType
from backend.shared.schemas.quality import Severity
from backend.worker.pipeline.errors import PipelineStageError
from backend.worker.pipeline.normalize import normalize_form
from tests.coi_helpers import leaf, sample, section

IMIE_ID = "00000000-0000-4000-8000-0000000000a1"


def _raw() -> dict[str, Any]:
    data = sample()
    field = leaf(data, 1, 0, 1)  # miejscowosc
    field.update({"source_text": "Miejscowość", "page": 2, "element_id": "txt-9"})
    return data


def _codes(issues: list[Any]) -> set[str]:
    return {i.code for i in issues}


def test_sources_moved_to_report() -> None:
    raw = _raw()
    field_id = leaf(raw, 1, 0, 1)["id"]
    normalized = normalize_form(raw)
    (source,) = [s for fid, s in normalized.sources.items() if str(fid) == field_id]
    assert (source.text, source.page, source.element_id) == ("Miejscowość", 2, "txt-9")
    assert "source_text" not in str(normalized.form.to_coi_json())


def test_valid_form_has_no_notes() -> None:
    normalized = normalize_form(sample())
    assert normalized.issues == []
    assert normalized.form.to_coi_json() == sample()


def test_leaf_on_tab_is_wrapped_in_section() -> None:
    raw = sample()
    info = leaf(raw, 3, 0, 0) | {"title": "Podgląd", "states": []}
    raw["tabs"][3]["questions"] = [{"x": 0, "y": 0, "rows": 1, "cols": 12, "field": info}]
    normalized = normalize_form(raw)
    (question,) = normalized.form.tabs[3].questions
    assert question.field.type is FieldType.SECTION
    assert question.field.title == "Podgląd"
    assert question.field.repeating_section_grid[0].field.type is FieldType.INFO_SECTION
    assert _codes(normalized.issues) == {"WRAPPED_IN_SECTION"}
    assert (question.x, question.y, question.rows, question.cols) == (0, 0, 2, 3)


def test_keys_not_allowed_for_type_are_removed_and_reported() -> None:
    raw = sample()
    uploader = leaf(raw, 0, 4, 1)  # FILE_UPLOADER
    uploader["multiLevelTree"] = True
    telefon = leaf(raw, 2, 0, 0)  # TEXT_FIELD
    telefon["options"] = [{"name": "Komórkowy"}]
    normalized = normalize_form(raw)
    notes = [i for i in normalized.issues if i.code == "KEY_NOT_ALLOWED_REMOVED"]
    assert len(notes) == 2
    assert "multiLevelTree" in notes[0].message
    assert "opcje" in notes[1].message
    assert all(n.severity is Severity.INFO for n in notes)


def test_validators_not_allowed_for_type_are_removed() -> None:
    raw = sample()
    leaf(raw, 2, 1, 0)["validation"] = ["REQUIRED"]  # CONSENT_SECTION — wymagalność w consents
    leaf(raw, 0, 1, 0)["validation"] = ["REQUIRED", "PESEL"]  # RADIO — tylko REQUIRED
    leaf(raw, 0, 2, 0)["validation"] = {}  # jak w eksporcie COI
    normalized = normalize_form(raw)
    dropped = [i for i in normalized.issues if i.code == "VALIDATOR_NOT_ALLOWED_REMOVED"]
    assert [d.severity for d in dropped] == [Severity.WARNING, Severity.WARNING]
    out = normalized.form.to_coi_json()
    assert leaf(out, 2, 1, 0)["validation"] == []
    assert leaf(out, 0, 1, 0)["validation"] == ["REQUIRED"]
    assert leaf(out, 0, 2, 0)["validation"] == []


def test_layout_follows_coi_single_column_grid() -> None:
    """COI: x=0, cols=3; pole 1 wiersz, sekcja = pola + 1; y i questionOrder rosną od 0."""
    raw = sample()
    for question in raw["tabs"][0]["questions"]:
        question.update(x=2, cols=1, rows=1)
        for child in question["field"]["repeatingSectionGrid"]:
            child.update(x=1, cols=12, rows=4)
    grid = section(raw, 0, 2)["repeatingSectionGrid"]  # twoje_dane: odwrócona kolejność
    for i, child in enumerate(reversed(grid)):
        child["y"] = i
    normalized = normalize_form(raw)
    out = normalized.form.to_coi_json()
    questions = out["tabs"][0]["questions"]
    assert [(q["x"], q["y"], q["rows"], q["cols"]) for q in questions] == [
        (0, 0, 2, 3), (0, 2, 3, 3), (0, 5, 4, 3), (0, 9, 4, 3), (0, 13, 3, 3)
    ]  # fmt: skip
    assert [q["field"]["questionOrder"] for q in questions] == [0, 2, 5, 9, 13]
    children = questions[2]["field"]["repeatingSectionGrid"]
    assert [c["field"]["techName"] for c in children] == ["pesel", "nazwisko", "imie"]
    assert [(c["y"], c["rows"], c["cols"], c["field"]["questionOrder"]) for c in children] == [
        (0, 1, 3, 0), (1, 1, 3, 1), (2, 1, 3, 2)
    ]  # fmt: skip


def test_descriptions_become_html_like_coi_editor() -> None:
    raw = sample()
    section(raw, 0, 0)["description"] = ""
    leaf(raw, 1, 0, 5)["description"] = "Opisz <krótko>\n\nDrugi akapit"  # TEXTAREA
    out = normalize_form(raw).form.to_coi_json()
    assert section(out, 0, 0)["description"] == "<html><head></head><body></body></html>"
    assert leaf(out, 1, 0, 5)["description"] == (
        "<html><head></head><body><p>Opisz &lt;krótko&gt;</p><p>Drugi akapit</p></body></html>"
    )


def test_legacy_grid_key_and_option_conventions() -> None:
    raw = sample()
    radio = leaf(raw, 0, 1, 0)
    radio["options"] = [{"id": "3f0c", "name": "Własnym imieniu"}]
    select = leaf(raw, 1, 0, 0)  # SELECT „Kraje”
    select["defaultValue"] = "Polska"
    legacy = section(raw, 0, 1)
    legacy["repetingSectionGrid"] = legacy.pop("repeatingSectionGrid")
    out = normalize_form(raw).form.to_coi_json()
    assert leaf(out, 0, 1, 0)["options"] == [
        {"id": "Własnym imieniu", "name": "Własnym imieniu", "techName": "wlasnym_imieniu"}
    ]
    assert leaf(out, 1, 0, 0)["defaultValue"] == ["Polska"]


def test_external_system_without_source_is_flagged() -> None:
    raw = sample()
    field = leaf(raw, 0, 2, 0)  # imie (MLPZ)
    del field["defaultValue"]
    normalized = normalize_form(raw)
    (issue,) = [i for i in normalized.issues if i.code == "INITIAL_DATA_SOURCE_MISSING"]
    assert issue.severity is Severity.WARNING
    imie = normalized.form.tabs[0].questions[2].field.repeating_section_grid[0].field
    assert getattr(imie, "default_value_type", None) is None


def test_invalid_tech_names_fixed_with_jsonata_references() -> None:
    raw = sample()
    section(raw, 0, 1)["techName"] = "1_podmiot"
    leaf(raw, 0, 1, 0)["techName"] = "2_w_czyim_imieniu"
    for target in (section(raw, 0, 3), leaf(raw, 0, 1, 1)):
        for state in target["states"]:
            state["query"] = (
                state["query"]
                .replace("'podmiot_wniosku'", "'1_podmiot'")
                .replace("'w_czyim_imieniu'", "'2_w_czyim_imieniu'")
            )
    normalized = normalize_form(raw)
    coi = normalized.form.to_coi_json()
    assert section(coi, 0, 1)["techName"] == "sekcja_1_podmiot"
    assert leaf(coi, 0, 1, 0)["techName"] == "pole_2_w_czyim_imieniu"
    query = section(coi, 0, 3)["states"][0]["query"]
    assert "'sekcja_1_podmiot' in field.techName" in query
    assert "'pole_2_w_czyim_imieniu' in field.techName" in query
    assert len([i for i in normalized.issues if i.code == "TECHNAME_NORMALIZED"]) == 2


def test_proposals_and_conflicts_mapped() -> None:
    raw = sample()
    raw["dictionary_proposals"] = [
        {
            "field_id": "czy_oplata",
            "name": "Opłata skarbowa",
            "values": ["Tak", "Nie"],
            "structure": {"entries": [{"code": "tak", "label": "Tak"}]},
            "usage": "lista",
        }
    ]
    raw["conflicts"] = [
        {
            "field_id": "kod_pocztowy",
            "attribute": "validation",
            "llm_value": [],
            "rule_value": ["ZIP_CODE"],
        }
    ]
    normalized = normalize_form(raw)
    (proposal,) = normalized.proposals
    assert (proposal.tech_name, proposal.entries[0].label) == ("czy_oplata", "Tak")
    (conflicts,) = normalized.conflicts.values()
    assert conflicts[0]["attribute"] == "validation"


def test_unfixable_result_is_stage_error() -> None:
    raw = sample()
    leaf(raw, 1, 0, 1)["type"] = "SIGNATURE"
    with pytest.raises(PipelineStageError, match="niezgodny ze schematem COI"):
        normalize_form(raw)


def test_choice_without_options_becomes_text_field() -> None:
    raw = sample()
    radio = leaf(raw, 0, 1, 0)
    radio["options"] = []
    normalized = normalize_form(raw)
    assert leaf(normalized.form.to_coi_json(), 0, 1, 0)["type"] == "TEXT_FIELD"
    assert "CHOICE_WITHOUT_OPTIONS" in _codes(normalized.issues)
