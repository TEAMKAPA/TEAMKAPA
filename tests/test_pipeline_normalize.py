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
    assert question.field.repeting_section_grid[0].field.type is FieldType.INFO_SECTION
    assert {"WRAPPED_IN_SECTION", "KEY_NOT_ALLOWED_REMOVED"} <= _codes(normalized.issues)


def test_keys_not_allowed_for_type_are_removed_and_reported() -> None:
    raw = sample()
    uploader = leaf(raw, 0, 4, 1)  # FILE_UPLOADER
    uploader["readOnly"] = True
    consent = leaf(raw, 2, 1, 0)  # CONSENT_SECTION
    consent.update({"title": "Zgoda", "validation": ["REQUIRED"], "states": []})
    normalized = normalize_form(raw)
    notes = [i for i in normalized.issues if i.code == "KEY_NOT_ALLOWED_REMOVED"]
    assert len(notes) == 2
    assert "readOnly" in notes[0].message
    assert "title" in notes[1].message and "validation" in notes[1].message
    assert all(n.severity is Severity.INFO for n in notes)


def test_external_system_without_source_is_flagged() -> None:
    raw = sample()
    field = leaf(raw, 0, 2, 0)  # imie (MLPZ)
    del field["defaultValue"]
    normalized = normalize_form(raw)
    (issue,) = [i for i in normalized.issues if i.code == "INITIAL_DATA_SOURCE_MISSING"]
    assert issue.severity is Severity.WARNING
    imie = normalized.form.tabs[0].questions[2].field.repeting_section_grid[0].field
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
