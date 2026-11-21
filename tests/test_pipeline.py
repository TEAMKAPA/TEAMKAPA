"""Unit tests for Eden AI LLM client, Steps 05-08, COI rules, and model comparison."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any
from urllib import request as urllib_request

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.worker.clients.llm_client import (  # noqa: E402
    DEFAULT_EDENAI_BASE_URL,
    DEFAULT_LLM_MODEL,
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
from eval.compare_models import (  # noqa: E402
    DEFAULT_MODELS,
    compare_models,
    format_comparison_table,
)

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
    def test_edenai_defaults_and_env_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EDENAI_API_KEY", "test-edenai-secret")
        monkeypatch.setenv("EDENAI_BASE_URL", "https://api.eu.edenai.run/v3")
        monkeypatch.setenv("LLM_MODEL", "scaleway/llama-3.3-70b-instruct")
        monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "45")
        monkeypatch.setenv("LLM_DEMO_MODE", "false")

        client = LLMClient()
        assert client.config.api_key == "test-edenai-secret"
        assert client.config.base_url == "https://api.eu.edenai.run/v3"
        assert client.config.model == "scaleway/llama-3.3-70b-instruct"
        assert client.config.timeout_seconds == 45.0
        assert client.config.offline is False
        assert "test-edenai-secret" not in repr(client.config)

    def test_edenai_http_request_and_response(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, Any] = {}

        class FakeHTTPResponse:
            def __enter__(self) -> FakeHTTPResponse:
                return self

            def __exit__(self, *_args: object) -> None:
                return None

            def read(self) -> bytes:
                body = {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": json.dumps({"status": "OK"}),
                            }
                        }
                    ]
                }
                return json.dumps(body).encode("utf-8")

        def fake_urlopen(req: urllib_request.Request, timeout: float) -> FakeHTTPResponse:
            assert isinstance(req.data, bytes)
            captured["url"] = req.full_url
            captured["method"] = req.get_method()
            captured["auth"] = req.get_header("Authorization")
            captured["content_type"] = req.get_header("Content-type")
            captured["body"] = json.loads(req.data.decode("utf-8"))
            captured["timeout"] = timeout
            return FakeHTTPResponse()

        monkeypatch.setattr(urllib_request, "urlopen", fake_urlopen)

        client = LLMClient(
            LLMConfig(
                model=DEFAULT_LLM_MODEL,
                api_key="fake-api-key",
                base_url=DEFAULT_EDENAI_BASE_URL,
                timeout_seconds=12.0,
                offline=False,
            )
        )
        schema = {
            "type": "object",
            "required": ["status"],
            "properties": {"status": {"type": "string", "enum": ["OK"]}},
        }

        result = client.generate_structured("Sprawdź status", schema)
        assert result == {"status": "OK"}
        assert captured["url"] == f"{DEFAULT_EDENAI_BASE_URL}/chat/completions"
        assert captured["method"] == "POST"
        assert captured["auth"] == "Bearer fake-api-key"
        assert captured["content_type"] == "application/json"
        assert captured["timeout"] == 12.0
        assert captured["body"]["model"] == DEFAULT_LLM_MODEL
        assert captured["body"]["response_format"] == {"type": "json_object"}
        assert captured["body"]["messages"][1]["content"] == "Sprawdź status"

    def test_missing_api_key_raises_structured_output_error(self) -> None:
        schema = {"type": "object", "required": ["status"], "properties": {}}
        client = LLMClient(LLMConfig(model=DEFAULT_LLM_MODEL, api_key="", offline=False))
        with pytest.raises(StructuredOutputError, match="EDENAI_API_KEY"):
            client.generate_structured("Podaj status", schema)

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
            LLMConfig(
                model="scaleway/qwen3-235b-a22b-instruct-2507",
                max_retries=2,
                offline=False,
            ),
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
            LLMConfig(model="mistral/mistral-small-latest", max_retries=1, offline=False),
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
            LLMConfig(model=DEFAULT_LLM_MODEL, offline=False),
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
        tab1_sections = {q["field"]["techName"]: q["field"] for q in tabs[0]["questions"]}
        assert tab1_sections["dane_podmiotu"]["states"][0]["type"] == "SHOW"
        assert "w_czyim_imieniu" in tab1_sections["dane_podmiotu"]["states"][0]["query"]

        addr_fields = {
            c["field"]["techName"]: c["field"]
            for c in tab1_sections["adres_zamieszkania"]["repetingSectionGrid"]
        }
        woj_state = addr_fields["wojewodztwo"]["states"][0]
        assert woj_state["type"] == "SHOW"
        assert '"Polska" in tabs[' in woj_state["query"]
        assert "repeatingSectionGrid['kraj' in field.techName]" in woj_state["query"]

    def test_build_jsonata_repeating_and_negated_queries(self) -> None:
        neg = build_jsonata_query("Dane", "adres_zamieszkania", "kraj", "Polska", negated=True)
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
    def test_compare_models_offline(self) -> None:
        assert DEFAULT_MODELS == (
            "scaleway/qwen3-235b-a22b-instruct-2507",
            "scaleway/llama-3.3-70b-instruct",
            "scaleway/gemma-3-27b-it",
            "mistral/mistral-small-latest",
        )
        results = compare_models(offline=True)
        assert len(results) == len(DEFAULT_MODELS)
        by_model = {r["model"]: r for r in results}
        assert set(by_model.keys()) == set(DEFAULT_MODELS)

        for row in results:
            assert 0.0 < row["precision"] <= 1.0
            assert 0.0 < row["recall"] <= 1.0
            assert 0.0 < row["f1"] <= 1.0
            assert row["schema_compliance"] == 1.0
            assert row["doc_type_top1_acc"] == 1.0
            assert row["source_metadata_rate"] == 1.0

        table = format_comparison_table(results)
        for model_name in DEFAULT_MODELS:
            assert model_name in table

    def test_compare_models_live_uses_edenai_client_mocked(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        requested_models: list[str] = []
        requested_urls: set[str] = set()
        requested_auth: set[str | None] = set()

        class FakeHTTPResponse:
            def __init__(self, payload: dict[str, Any]) -> None:
                self._payload = payload

            def __enter__(self) -> FakeHTTPResponse:
                return self

            def __exit__(self, *_args: object) -> None:
                return None

            def read(self) -> bytes:
                body = {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": json.dumps(self._payload, ensure_ascii=False),
                            }
                        }
                    ]
                }
                return json.dumps(body).encode("utf-8")

        def fake_urlopen(req: urllib_request.Request, timeout: float) -> FakeHTTPResponse:
            assert timeout > 0
            assert isinstance(req.data, bytes)
            requested_urls.add(req.full_url)
            requested_auth.add(req.get_header("Authorization"))
            body = json.loads(req.data.decode("utf-8"))
            requested_models.append(body["model"])
            prompt = str(body["messages"][1]["content"])

            if "strukturę stron" in prompt:
                payload: dict[str, Any] = {
                    "title": "Wniosek testowy",
                    "tabs": [
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
                        },
                        {
                            "title": "Podsumowanie i podgląd",
                            "type": "FINISH",
                            "sections": [
                                {
                                    "title": "Podgląd",
                                    "techName": "podsumowanie_i_podglad",
                                    "type": "INFO_SECTION",
                                    "description": "Sprawdź dane na podglądzie.",
                                }
                            ],
                        },
                    ],
                }
            elif "Wyekstrahuj pola" in prompt:
                payload = {
                    "fields": [
                        {
                            "section_tech_name": "twoje_dane",
                            "techName": "imie",
                            "title": "Imię",
                            "type": "TEXT_FIELD",
                            "validation": ["REQUIRED"],
                            "source_text": "Imię",
                            "page": 1,
                            "element_id": "e1",
                        }
                    ]
                }
            else:
                payload = {"dependencies": []}
            return FakeHTTPResponse(payload)

        monkeypatch.setattr(urllib_request, "urlopen", fake_urlopen)

        results = compare_models(
            models=DEFAULT_MODELS,
            offline=False,
            api_key="fake-edenai-key",
            base_url=DEFAULT_EDENAI_BASE_URL,
        )

        assert requested_urls == {f"{DEFAULT_EDENAI_BASE_URL}/chat/completions"}
        assert requested_auth == {"Bearer fake-edenai-key"}
        assert set(requested_models) == set(DEFAULT_MODELS)
        assert [row["model"] for row in results] == list(DEFAULT_MODELS)
        for row in results:
            assert row["precision"] == 1.0
            assert 0.0 < row["recall"] <= 1.0
            assert row["schema_compliance"] == 1.0
            assert row["doc_type_top1_acc"] == 1.0
            assert row["source_metadata_rate"] == 1.0
