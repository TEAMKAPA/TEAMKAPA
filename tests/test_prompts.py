"""Zapytania do modelu (etapy 06–08) zawierają treść dokumentu, kontekst etapu i schemat."""

import json
from typing import Any

from backend.worker.clients.llm_client import LLMClient, LLMConfig
from backend.worker.pipeline.prompts import (
    SYSTEM_PROMPT,
    dependencies_prompt,
    document_listing,
    fields_prompt,
    structure_prompt,
)
from backend.worker.pipeline.step06_structure import (
    STRUCTURE_SCHEMA,
    _build_mock_structure,
    run_step06,
)
from backend.worker.pipeline.step07_fields import FIELDS_SCHEMA, _build_mock_fields, run_step07
from backend.worker.pipeline.step08_dependencies import DEPENDENCIES_SCHEMA, run_step08

DOCUMENT: dict[str, Any] = {
    "text": "Wniosek o wydanie zaświadczenia\nImię i nazwisko\nPESEL",
    "elements": [
        {"element_id": "p1-b1", "page": 1, "text": "Wniosek o wydanie zaświadczenia"},
        {"element_id": "p1-b2", "page": 1, "text": "Imię   i\nnazwisko"},
        {"element_id": "p2-b1", "page": 2, "text": "PESEL"},
        {"element_id": "p2-b2", "page": 2, "text": "   "},
    ],
}


def test_document_listing_has_ids_pages_and_collapsed_whitespace() -> None:
    assert document_listing(DOCUMENT).splitlines() == [
        "[p1-b1|s.1] Wniosek o wydanie zaświadczenia",
        "[p1-b2|s.1] Imię i nazwisko",
        "[p2-b1|s.2] PESEL",
    ]


def test_document_listing_respects_limit() -> None:
    listing = document_listing(DOCUMENT, max_chars=50)
    assert listing.splitlines()[0].startswith("[p1-b1|s.1]")
    assert "pominięto 2 elementów" in listing


def test_structure_prompt_contains_document_rules_and_schema() -> None:
    prompt = structure_prompt(DOCUMENT, "Wniosek o zaświadczenie", STRUCTURE_SCHEMA)
    assert "[p2-b1|s.2] PESEL" in prompt
    assert "Strona końcowa" in prompt and "Kontakt i zgody" in prompt
    assert "authority" in prompt and "Wójt Gminy Elbląg" in prompt
    assert json.dumps(STRUCTURE_SCHEMA, ensure_ascii=False, separators=(",", ":")) in prompt


def test_fields_prompt_lists_only_subject_sections() -> None:
    structure = run_step06(DOCUMENT)
    prompt = fields_prompt(DOCUMENT, structure, None, FIELDS_SCHEMA)
    subject = [
        q["field"]["techName"]
        for tab in structure["tabs"]
        if tab["type"] == "MAIN" and tab.get("origin") != "standard"
        for q in tab["questions"]
    ]
    assert subject and all(f"- {tech}:" in prompt for tech in subject)
    # Sekcje standardowe (szablon COI) i strona końcowa nie dostają pól od modelu.
    assert "- twoje_dane:" not in prompt and "- podmiot_wniosku:" not in prompt
    assert "podglad_uzupelnionego_wniosku" not in prompt
    assert "„Kraje”" in prompt
    assert "[p1-b2|s.1] Imię i nazwisko" in prompt


def test_dependencies_prompt_lists_fields_with_options() -> None:
    form = run_step07(DOCUMENT, run_step06(DOCUMENT))
    prompt = dependencies_prompt(DOCUMENT, form, DEPENDENCIES_SCHEMA)
    radios = [
        child["field"]
        for tab in form["tabs"]
        for q in tab["questions"]
        for child in q["field"]["repeatingSectionGrid"]
        if child["field"]["type"] == "RADIO"
    ]
    for radio in radios:
        assert f"- {radio['techName']} (RADIO)" in prompt
        assert all(o["name"] in prompt for o in radio["options"])
    assert "[p2-b1|s.2] PESEL" in prompt


def test_steps_send_system_prompt_and_document_to_model() -> None:
    """Każdy etap wysyła do modelu instrukcję systemową i treść dokumentu."""
    calls: list[tuple[str, str]] = []

    def transport(prompt: str, system_prompt: str, attempt: int) -> str:
        calls.append((prompt, system_prompt))
        if "Zaprojektuj strony" in prompt:
            return json.dumps(offline_structure)
        if "Wyodrębnij pola" in prompt:
            return json.dumps(offline_fields)
        return json.dumps({"dependencies": []})

    offline_structure = _build_mock_structure(DOCUMENT, None)
    structure = run_step06(DOCUMENT)
    offline_fields = _build_mock_fields(DOCUMENT, None, structure)

    client = LLMClient(LLMConfig(model="m", api_key="k", offline=False), transport=transport)
    form = run_step06(DOCUMENT, llm_client=client)
    form = run_step07(DOCUMENT, form, llm_client=client)
    run_step08(form, llm_client=client, document=DOCUMENT)

    assert len(calls) == 3
    assert all(system == SYSTEM_PROMPT for _, system in calls)
    assert all("[p2-b1|s.2] PESEL" in prompt for prompt, _ in calls)
