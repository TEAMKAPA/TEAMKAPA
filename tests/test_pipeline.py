"""Unit tests for local LLM client, Steps 05-08, COI rules, and model comparison."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.worker.clients.llm_client import (  # noqa: E402
    LLMClient,
    LLMConfig,
    StructuredOutputError,
)
from backend.worker.pipeline.step05_doc_type import (  # noqa: E402
    TEMPLATE_REGISTRY,
    classify_document,
    run_step05,
)
from backend.worker.pipeline.step06_structure import (  # noqa: E402
    COI_COMPONENT_TYPES,
    COI_SECTION_TYPES,
    COI_VALIDATORS,
    run_step06,
    validate_coi_structure,
)
from backend.worker.pipeline.step07_fields import run_step07  # noqa: E402
from backend.worker.pipeline.step08_dependencies import (  # noqa: E402
    build_jsonata_query,
    run_step08,
    validate_form_definition,
)
from eval.compare_models import compare_models  # noqa: E402


SAMPLE_DOC: dict[str, Any] = {
    "title": "Wniosek o wpis do CEIDG (CEIDG-1)",
    "elements": [
        {"element_id": "el_1", "page": 1, "text": "Kod formularza: CEIDG-1"},
        {"element_id": "el_2", "page": 1, "text": "Urząd Miasta / CEIDG"},
        {"element_id": "el_3", "page": 1, "text": "Imię, Nazwisko, Numer PESEL"},
        {
            "element_id": "el_4",
            "page": 1,
            "label": "Nazwa firmy",
            "text": "Nazwa firmy przedsiębiorcy",
            "required": True,
        },
        {
            "element_id": "el_5",
            "page": 2,
            "label": "Data rozpoczęcia działalności",
            "text": "Data rozpoczęcia działalności gospodarczej",
            "required": True,
        },
        {
            "element_id": "el_6",
            "page": 2,
            "label": "Forma opodatkowania",
            "text": "Forma opodatkowania: Skala / Liniowy / Ryczałt",
            "options": ["Skala podatkowa", "Podatek liniowy", "Ryczałt"],
            "required": True,
        },
        {
            "element_id": "el_7",
            "page": 2,
            "text": "Wyrażam zgodę na przetwarzanie danych osobowych (RODO).",
        },
    ],
}


class TestLLMClientStructuredOutput:
    def test_pllum_and_bielik_presets(self) -> None:
        pllum = LLMConfig(model="pllum")
        bielik = LLMConfig(model="bielik")
        assert "pllum" in pllum.canonical_model.lower()
        assert "bielik" in bielik.canonical_model.lower()
        assert pllum.model_family == "pllum"
        assert bielik.model_family == "bielik"

    def test_retry_succeeds_after_invalid_json_and_schema_mismatch(self) -> None:
        schema = {
            "type": "object",
            "required": ["name", "count"],
            "properties": {
                "name": {"type": "string", "minLength": 1},
                "count": {"type": "integer", "minimum": 1},
            },
        }
        responses = [
            "not valid json at all",
            json.dumps({"name": "test"}),  # missing required 'count'
            '```json\n{"name": "ok", "count": 3}\n```',
        ]
        prompts_seen: list[str] = []

        def fake_transport(prompt: str, _system: str, attempt: int) -> str:
            prompts_seen.append(prompt)
            return responses[attempt]

        client = LLMClient(
            LLMConfig(model="pllum", max_retries=2, offline=False),
            transport=fake_transport,
        )
        result = client.generate_structured("Podaj dane", schema)

        assert result == {"name": "ok", "count": 3}
        assert len(prompts_seen) == 3
        assert "Invalid JSON" in prompts_seen[1]
        assert "missing required key 'count'" in prompts_seen[2]

    def test_rejects_after_exhausting_retries(self) -> None:
        schema = {
            "type": "object",
            "required": ["status"],
            "properties": {"status": {"type": "string", "enum": ["OK"]}},
        }

        def bad_transport(_prompt: str, _system: str, _attempt: int) -> str:
            return json.dumps({"status": "WRONG"})

        client = LLMClient(
            LLMConfig(model="bielik", max_retries=1, offline=False),
            transport=bad_transport,
        )
        with pytest.raises(StructuredOutputError, match="failed schema validation"):
            client.generate_structured("Podaj status", schema)


class TestStep05DocType:
    def test_returns_top_5_candidates_sorted_descending(self) -> None:
        assert len(TEMPLATE_REGISTRY) >= 5
        result = classify_document(
            "Formularz CEIDG-1 Wniosek o wpis do ewidencji działalności gospodarczej "
            "NIP REGON PKD forma opodatkowania"
        )
        candidates = result["top_candidates"]

        assert len(candidates) == 5
        scores = [c["score"] for c in candidates]
        assert scores == sorted(scores, reverse=True)
        assert result["doc_type"] == "wniosek_ceidg"
        assert result["selected_template"]["code"] == "CEIDG-1"

    def test_run_step05_recognizes_tree_removal_form(self) -> None:
        doc = {
            "title": "Wniosek o zezwolenie na usunięcie drzewa lub krzewu",
            "text": "Kod DRZ-01, gatunek drzewa, obwód pnia, działka ewidencyjna",
        }
        result = run_step05(doc)
        assert len(result["top_candidates"]) == 5
        assert result["doc_type"] == "wniosek_usuniecie_drzewa"
        assert result["selected_template"]["template_id"] == "tpl_usuniecie_drzewa"


class TestStep06StructureAndCOIRules:
    def test_finish_tab_has_only_info_section_and_no_nested_sections(self) -> None:
        step05 = run_step05(SAMPLE_DOC)
        structure = run_step06(SAMPLE_DOC, template=step05["selected_template"])

        assert len(structure["title"]) <= 255
        assert structure["title"].startswith("Wniosek")
        tabs = structure["tabs"]
        assert len(tabs) >= 2
        assert all(t["type"] == "MAIN" for t in tabs[:-1])
        assert tabs[-1]["type"] == "FINISH"

        # FINISH tab must contain EXCLUSIVELY INFO_SECTION (zero input fields).
        finish_fields = [q["field"] for q in tabs[-1]["questions"]]
        assert len(finish_fields) >= 1
        assert all(f["type"] == "INFO_SECTION" for f in finish_fields)

        # Logical COI section ordering on Tab 1.
        tab1_techs = [q["field"]["techName"] for q in tabs[0]["questions"]]
        assert tab1_techs.index("urzad_wniosku") < tab1_techs.index("podmiot_wniosku")
        assert tab1_techs.index("podmiot_wniosku") < tab1_techs.index("twoje_dane")
        assert tab1_techs.index("twoje_dane") < tab1_techs.index("adres_zamieszkania")
        assert validate_coi_structure(structure) == []

    def test_validator_rejects_nested_section_and_finish_input(self) -> None:
        bad_form = {
            "title": "Wniosek testowy",
            "tabs": [
                {
                    "id": "t1",
                    "title": "Dane",
                    "type": "MAIN",
                    "questions": [
                        {
                            "x": 0,
                            "y": 0,
                            "rows": 1,
                            "cols": 12,
                            "field": {
                                "id": "s1",
                                "type": "SECTION",
                                "techName": "sec_outer",
                                "title": "Outer",
                                "repetingSectionGrid": [
                                    {
                                        "x": 0,
                                        "y": 0,
                                        "rows": 1,
                                        "cols": 12,
                                        "field": {
                                            "id": "s2",
                                            "type": "SECTION",
                                            "techName": "sec_inner",
                                            "title": "Inner",
                                        },
                                    }
                                ],
                            },
                        }
                    ],
                },
                {
                    "id": "t2",
                    "title": "Koniec",
                    "type": "FINISH",
                    "questions": [
                        {
                            "x": 0,
                            "y": 0,
                            "rows": 1,
                            "cols": 12,
                            "field": {
                                "id": "f1",
                                "type": "TEXT_FIELD",
                                "techName": "illegal_input",
                                "title": "Pole input",
                            },
                        }
                    ],
                },
            ],
        }
        errors = validate_coi_structure(bad_form)
        assert any("Nested section" in err for err in errors)
        assert any("FINISH tab may only contain INFO_SECTION" in err for err in errors)


class TestStep07FieldsAndStep08Dependencies:
    def test_all_fields_have_source_metadata_and_consents_use_consent_section(
        self,
    ) -> None:
        step05 = run_step05(SAMPLE_DOC)
        step06 = run_step06(SAMPLE_DOC, template=step05["selected_template"])
        step07 = run_step07(SAMPLE_DOC, step06, template=step05["selected_template"])

        all_fields: list[dict[str, Any]] = []
        for tab in step07["tabs"]:
            if tab["type"] == "FINISH":
                for q in tab["questions"]:
                    f = q["field"]
                    assert f["type"] == "INFO_SECTION"
                    assert f["source_text"]
                    assert isinstance(f["page"], int) and f["page"] >= 1
                    assert "element_id" in f
                continue

            for q in tab["questions"]:
                sec = q["field"]
                assert sec["type"] in COI_SECTION_TYPES
                for child in sec["repetingSectionGrid"]:
                    f = child["field"]
                    all_fields.append(f)
                    assert f["type"] in (COI_COMPONENT_TYPES - COI_SECTION_TYPES)
                    assert all(v in COI_VALIDATORS for v in f.get("validation", []))
                    assert isinstance(f.get("source_text"), str) and f["source_text"]
                    assert isinstance(f.get("page"), int) and f["page"] >= 1
                    assert "element_id" in f

        by_tech = {f["techName"]: f for f in all_fields}

        # Authority is readOnly.
        assert by_tech["nazwa_urzedu"]["readOnly"] is True

        # Twoje dane: Imię, Nazwisko, PESEL readOnly + EXTERNAL_SYSTEM.
        for key in ("imie", "nazwisko", "pesel"):
            assert by_tech[key]["readOnly"] is True
            assert by_tech[key]["defaultValueType"] == "EXTERNAL_SYSTEM"
        assert "PESEL" in by_tech["pesel"]["validation"]

        # Consents MUST be CONSENT_SECTION (never CHECKBOX or RADIO).
        consent_fields = [f for f in all_fields if f["type"] == "CONSENT_SECTION"]
        assert len(consent_fields) >= 1
        assert consent_fields[0]["consentSectionConfig"]["consents"]
        for f in all_fields:
            if "zgod" in f["title"].lower() or "oświadcz" in f["title"].lower():
                assert f["type"] == "CONSENT_SECTION"

    def test_llm_checkbox_consent_is_forced_to_consent_section(self) -> None:
        step05 = run_step05(SAMPLE_DOC)
        step06 = run_step06(SAMPLE_DOC, template=step05["selected_template"])

        # Simulate an LLM mistakenly classifying a consent as CHECKBOX.
        def mock_bad_consent_transport(_p: str, _s: str, _a: int) -> str:
            return json.dumps(
                {
                    "fields": [
                        {
                            "section_tech_name": "zgody_i_oswiadczenia",
                            "techName": "zgoda_rodo",
                            "title": "Wyrażam zgodę na przetwarzanie danych",
                            "type": "CHECKBOX",
                            "validation": ["REQUIRED"],
                            "source_text": "Wyrażam zgodę na przetwarzanie danych",
                            "page": 2,
                            "element_id": "el_7",
                        }
                    ]
                }
            )

        client = LLMClient(
            LLMConfig(model="pllum", offline=False),
            transport=mock_bad_consent_transport,
        )
        step07 = run_step07(SAMPLE_DOC, step06, llm_client=client)
        fields = [
            child["field"]
            for tab in step07["tabs"]
            if tab["type"] == "MAIN"
            for q in tab["questions"]
            for child in q["field"]["repetingSectionGrid"]
        ]
        assert len(fields) == 1
        assert fields[0]["type"] == "CONSENT_SECTION"
        assert fields[0]["consentSectionConfig"]["required"] is True

    def test_step08_states_jsonata_and_flow_links(self) -> None:
        step05 = run_step05(SAMPLE_DOC)
        step06 = run_step06(SAMPLE_DOC, template=step05["selected_template"])
        step07 = run_step07(SAMPLE_DOC, step06, template=step05["selected_template"])
        form = run_step08(step07)

        assert validate_form_definition(form) == []

        # Verify flow.links connects every tab sequentially to FINISH.
        tabs = form["tabs"]
        links = form["flow"]["links"]
        assert len(links) == len(tabs) - 1
        for idx, link in enumerate(links):
            assert link["id"]
            assert link["source"] == tabs[idx]["id"]
            assert link["target"] == tabs[idx + 1]["id"]

        # Verify JSONata states on Polish address & proxy sections.
        tab1_sections = {
            q["field"]["techName"]: q["field"] for q in tabs[0]["questions"]
        }
        assert tab1_sections["dane_podmiotu"]["states"][0]["type"] == "SHOW"
        assert (
            "w_czyim_imieniu" in tab1_sections["dane_podmiotu"]["states"][0]["query"]
        )

        addr_fields = {
            c["field"]["techName"]: c["field"]
            for c in tab1_sections["adres_zamieszkania"]["repetingSectionGrid"]
        }
        woj_state = addr_fields["wojewodztwo"]["states"][0]
        assert woj_state["type"] == "SHOW"
        assert '"Polska" in tabs[' in woj_state["query"]
        assert "repeatingSectionGrid['kraj' in field.techName]" in woj_state["query"]

    def test_build_jsonata_repeating_and_negated_queries(self) -> None:
        neg = build_jsonata_query(
            "Dane", "adres_zamieszkania", "kraj", "Polska", negated=True
        )
        assert neg.startswith('not ("Polska" in tabs[')

        rep = build_jsonata_query(
            "Przedmiot",
            "przedmiot_wniosku",
            "gatunek",
            "Dąb",
            in_repeating_section=True,
        )
        assert "$repeatingSectionIndex" in rep
        assert "['gatunek' in fieldTechName].answers" in rep


class TestModelComparison:
    def test_compare_pllum_vs_bielik_offline(self) -> None:
        results = compare_models(offline=True)
        assert len(results) == 2
        by_model = {r["model"]: r for r in results}
        assert set(by_model.keys()) == {"PLLuM", "Bielik"}

        for row in results:
            assert 0.0 < row["precision"] <= 1.0
            assert 0.0 < row["recall"] <= 1.0
            assert 0.0 < row["f1"] <= 1.0
            assert row["schema_compliance"] == 1.0
            assert row["doc_type_top1_acc"] == 1.0
            assert row["source_metadata_rate"] == 1.0
