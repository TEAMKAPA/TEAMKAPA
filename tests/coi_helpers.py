"""Wspólne narzędzia testów modeli COI."""

import copy
import json
from pathlib import Path
from typing import Any

from backend.shared.schemas.coi_form import FormDefinition
from backend.shared.validators.form_rules import validate_form_rules

ROOT = Path(__file__).resolve().parents[1]
SAMPLE_FORM_PATH = ROOT / "tests" / "fixtures" / "sample_form.json"
OFFICIAL_SCHEMA_PATH = ROOT / "docs" / "coi" / "importowanyPlikJsonSchemat.json"

_SAMPLE: dict[str, Any] = json.loads(SAMPLE_FORM_PATH.read_text(encoding="utf-8"))


def sample() -> dict[str, Any]:
    """Świeża kopia przykładowego formularza (wolno ją mutować w teście)."""
    return copy.deepcopy(_SAMPLE)


def official_schema() -> dict[str, Any]:
    result: dict[str, Any] = json.loads(OFFICIAL_SCHEMA_PATH.read_text(encoding="utf-8"))
    return result


def rule_codes(data: dict[str, Any]) -> set[str]:
    return {i.code for i in validate_form_rules(FormDefinition.model_validate(data))}


def section(data: dict[str, Any], tab: int, idx: int) -> dict[str, Any]:
    result: dict[str, Any] = data["tabs"][tab]["questions"][idx]["field"]
    return result


def leaf(data: dict[str, Any], tab: int, sec: int, idx: int) -> dict[str, Any]:
    result: dict[str, Any] = section(data, tab, sec)["repetingSectionGrid"][idx]["field"]
    return result
