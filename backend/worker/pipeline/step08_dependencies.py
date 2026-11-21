"""Step 08: Conditional dependencies (JSONata states), flow.links, and validation."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from backend.worker.clients.llm_client import LLMClient
from backend.worker.pipeline.prompts import SYSTEM_PROMPT, dependencies_prompt
from backend.worker.pipeline.step06_structure import (
    COI_COMPONENT_TYPES,
    COI_SECTION_TYPES,
    COI_VALIDATORS,
    make_uuid,
    validate_coi_structure,
)

STATE_TYPES_FIELD: frozenset[str] = frozenset({"SHOW", "READ_ONLY"})
STATE_TYPES_SECTION: frozenset[str] = frozenset({"SHOW"})

DEPENDENCIES_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["dependencies"],
    "properties": {
        "dependencies": {
            "type": "array",
            "items": {
                "type": "object",
                "required": [
                    "target_kind",
                    "target_tech_name",
                    "state_type",
                    "source_field_tech_name",
                    "expected_value",
                ],
                "properties": {
                    "target_kind": {"type": "string", "enum": ["section", "field"]},
                    "target_tech_name": {"type": "string", "minLength": 1},
                    "state_type": {"type": "string", "enum": ["SHOW", "READ_ONLY"]},
                    "source_field_tech_name": {"type": "string", "minLength": 1},
                    "expected_value": {"type": "string", "minLength": 1},
                    "negated": {"type": "boolean"},
                },
            },
        }
    },
}


def build_jsonata_query(
    tab_title: str,
    section_tech_name: str,
    field_tech_name: str,
    expected_value: str,
    *,
    negated: bool = False,
    in_repeating_section: bool = False,
) -> str:
    """Build a COI-compliant JSONata condition expression."""
    safe_tab = tab_title.replace("'", "\\'")
    safe_val = expected_value.replace('"', '\\"')

    if in_repeating_section:
        expr = (
            f"\"{safe_val}\" in tabs['{safe_tab}' in title]"
            f".questions['{section_tech_name}' in field.techName]"
            f".field.__userAnswer[$repeatingSectionIndex]"
            f"['{field_tech_name}' in fieldTechName].answers"
        )
    else:
        expr = (
            f"\"{safe_val}\" in tabs['{safe_tab}' in title]"
            f".questions['{section_tech_name}' in field.techName]"
            f".field.repeatingSectionGrid['{field_tech_name}' in field.techName]"
            ".field.__userAnswer"
        )

    return f"not ({expr})" if negated else f"({expr})"


def _index_form_elements(
    form: dict[str, Any],
) -> tuple[dict[str, tuple[dict[str, Any], dict[str, Any]]], dict[str, dict[str, Any]]]:
    """Return lookups for fields -> (tab, section) and sections -> section_field."""
    field_locations: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    sections: dict[str, dict[str, Any]] = {}

    for tab in form.get("tabs", []):
        if tab.get("type") != "MAIN":
            continue
        for q in tab.get("questions", []):
            sec_field = q.get("field", {})
            sec_tech = sec_field.get("techName")
            if not sec_tech:
                continue
            sections[sec_tech] = sec_field
            for child in sec_field.get("repeatingSectionGrid", []):
                child_field = child.get("field", {})
                child_tech = child_field.get("techName")
                if child_tech:
                    field_locations[child_tech] = (tab, sec_field)

    return field_locations, sections


def _build_mock_dependencies(form: dict[str, Any]) -> dict[str, Any]:
    """Derive standard COI conditional dependencies from extracted sections/fields."""
    field_locations, sections = _index_form_elements(form)
    deps: list[dict[str, Any]] = []

    # 1. Address dependency: Polish address fields shown when Kraj == "Polska".
    if "kraj" in field_locations:
        for polish_field in ("wojewodztwo", "powiat", "gmina", "miejscowosc"):
            if polish_field in field_locations:
                deps.append(
                    {
                        "target_kind": "field",
                        "target_tech_name": polish_field,
                        "state_type": "SHOW",
                        "source_field_tech_name": "kraj",
                        "expected_value": "Polska",
                        "negated": False,
                    }
                )
        if "kod_pocztowy" in field_locations:
            deps.append(
                {
                    "target_kind": "field",
                    "target_tech_name": "kod_pocztowy",
                    "state_type": "SHOW",
                    "source_field_tech_name": "kraj",
                    "expected_value": "Polska",
                    "negated": False,
                }
            )

    # 2. Podmiot wniosku dependencies (COI standard).
    if "w_czyim_imieniu" in field_locations:
        if "jako_kto_wnioskujesz" in field_locations:
            deps.append(
                {
                    "target_kind": "field",
                    "target_tech_name": "jako_kto_wnioskujesz",
                    "state_type": "SHOW",
                    "source_field_tech_name": "w_czyim_imieniu",
                    "expected_value": "Osoby prawnej lub jednostki organizacyjnej",
                    "negated": False,
                }
            )
        if "dane_innej_osoby" in sections:
            deps.append(
                {
                    "target_kind": "section",
                    "target_tech_name": "dane_innej_osoby",
                    "state_type": "SHOW",
                    "source_field_tech_name": "w_czyim_imieniu",
                    "expected_value": "Innej osoby",
                    "negated": False,
                }
            )
        if "dane_podmiotu" in sections:
            deps.append(
                {
                    "target_kind": "section",
                    "target_tech_name": "dane_podmiotu",
                    "state_type": "SHOW",
                    "source_field_tech_name": "w_czyim_imieniu",
                    "expected_value": "Osoby prawnej lub jednostki organizacyjnej",
                    "negated": False,
                }
            )
        if "pelnomocnictwo" in sections:
            deps.append(
                {
                    "target_kind": "section",
                    "target_tech_name": "pelnomocnictwo",
                    "state_type": "SHOW",
                    "source_field_tech_name": "w_czyim_imieniu",
                    "expected_value": "Innej osoby",
                    "negated": False,
                }
            )

    return {"dependencies": deps}


def build_flow_links(tabs: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Build sequential flow.links connecting consecutive form tabs."""
    links: list[dict[str, str]] = []
    for idx in range(len(tabs) - 1):
        src_id = str(tabs[idx]["id"])
        tgt_id = str(tabs[idx + 1]["id"])
        links.append(
            {
                "id": make_uuid(f"link.{idx}.{src_id}.{tgt_id}"),
                "source": src_id,
                "target": tgt_id,
            }
        )
    return links


def validate_form_definition(form: dict[str, Any]) -> list[str]:
    """Validate complete COI FormDefinition rules (structure, fields, states, flow)."""
    errors = validate_coi_structure(form)
    tabs = form.get("tabs", [])

    # Check fields, validators, consent rule, source metadata, and states.
    for tab in tabs:
        if tab.get("type") != "MAIN":
            continue
        for q in tab.get("questions", []):
            sec = q.get("field", {})
            for st in sec.get("states", []):
                if st.get("type") not in STATE_TYPES_SECTION:
                    errors.append(
                        f"Section '{sec.get('techName')}' has invalid state "
                        f"type '{st.get('type')}'."
                    )
                if not st.get("query"):
                    errors.append(f"Section '{sec.get('techName')}' state is missing query.")

            grid = sec.get("repeatingSectionGrid", [])
            if not grid:
                errors.append(f"Section '{sec.get('techName')}' must contain at least 1 field.")
            for child in grid:
                f = child.get("field", {})
                ftype = f.get("type")
                if ftype not in COI_COMPONENT_TYPES or ftype in COI_SECTION_TYPES:
                    errors.append(f"Invalid field type '{ftype}' in '{f.get('techName')}'.")
                for val in f.get("validation", []):
                    if val not in COI_VALIDATORS:
                        errors.append(f"Unknown COI validator '{val}' on '{f.get('techName')}'.")
                if ftype == "CONSENT_SECTION" and not f.get("consentSectionConfig"):
                    errors.append(
                        f"CONSENT_SECTION '{f.get('techName')}' missing consentSectionConfig."
                    )
                if not f.get("source_text") or not isinstance(f.get("page"), int):
                    errors.append(f"Field '{f.get('techName')}' missing source_text or page.")
                if "element_id" not in f:
                    errors.append(f"Field '{f.get('techName')}' missing element_id key.")
                for st in f.get("states", []):
                    if st.get("type") not in STATE_TYPES_FIELD:
                        errors.append(
                            f"Field '{f.get('techName')}' has invalid state "
                            f"type '{st.get('type')}'."
                        )
                    if not st.get("query"):
                        errors.append(f"Field '{f.get('techName')}' state is missing query.")

    # Check flow.links connects all tabs.
    links = form.get("flow", {}).get("links", [])
    expected_links = max(0, len(tabs) - 1)
    if len(links) < expected_links:
        errors.append(
            f"flow.links must connect all tabs (expected >= {expected_links}, got {len(links)})."
        )
    tab_ids = {t.get("id") for t in tabs}
    for link in links:
        if link.get("source") not in tab_ids or link.get("target") not in tab_ids:
            errors.append(f"flow.links entry '{link.get('id')}' references unknown tab")

    return errors


def run_step08(
    form_with_fields: dict[str, Any],
    *,
    llm_client: LLMClient | None = None,
    document: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Attach JSONata conditional states and sequential flow.links to a form."""
    client = llm_client or LLMClient()
    form = deepcopy(form_with_fields)

    field_locations, sections = _index_form_elements(form)
    fields_by_tech: dict[str, dict[str, Any]] = {}
    for sec in sections.values():
        for child in sec.get("repeatingSectionGrid", []):
            cf = child.get("field", {})
            if cf.get("techName"):
                fields_by_tech[cf["techName"]] = cf

    prompt = dependencies_prompt(document, form, DEPENDENCIES_SCHEMA)
    llm_output = client.generate_structured(
        prompt,
        DEPENDENCIES_SCHEMA,
        system_prompt=SYSTEM_PROMPT,
        mock_response_fn=lambda _model: _build_mock_dependencies(form),
    )

    for dep in llm_output["dependencies"]:
        src_tech = dep["source_field_tech_name"]
        if src_tech not in field_locations:
            continue

        src_tab, src_sec = field_locations[src_tech]
        query = build_jsonata_query(
            tab_title=str(src_tab["title"]),
            section_tech_name=str(src_sec["techName"]),
            field_tech_name=src_tech,
            expected_value=str(dep["expected_value"]),
            negated=bool(dep.get("negated", False)),
            in_repeating_section=(src_sec.get("type") == "REPEATING_SECTION"),
        )

        target_kind = dep["target_kind"]
        target_tech = dep["target_tech_name"]
        state_type = dep["state_type"]

        if target_kind == "section" and target_tech in sections:
            if state_type not in STATE_TYPES_SECTION:
                state_type = "SHOW"
            states = sections[target_tech].setdefault("states", [])
            state_entry = {"type": state_type, "query": query}
            if state_entry not in states:
                states.append(state_entry)
        elif target_kind == "field" and target_tech in fields_by_tech:
            if state_type not in STATE_TYPES_FIELD:
                state_type = "SHOW"
            states = fields_by_tech[target_tech].setdefault("states", [])
            state_entry = {"type": state_type, "query": query}
            if state_entry not in states:
                states.append(state_entry)

    form["flow"] = {"links": build_flow_links(form.get("tabs", []))}

    errors = validate_form_definition(form)
    if errors:
        raise ValueError(f"Invalid COI FormDefinition: {'; '.join(errors)}")
    return form
