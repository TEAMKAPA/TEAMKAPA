"""Etap E.1: kody, bramki, pasma pewności, konfiguracja i modele fuzji."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from backend.shared.config import Settings
from backend.shared.schemas.coi_form import FieldType
from backend.shared.schemas.quality import (
    BoundingBox,
    ConfidenceLevel,
    Severity,
    confidence_level,
)
from backend.worker.vision.fusion import codes
from backend.worker.vision.fusion.config import FusionConfig, FusionSettings, load_fusion_config
from backend.worker.vision.fusion.models import (
    Decision,
    FieldRef,
    FusedElement,
    RepairProblem,
    banded,
)
from backend.worker.vision.models import ElementKind, ElementRole, ElementSource
from backend.worker.vision.vlm import codes as vlm

H, M, L = ConfidenceLevel.HIGH, ConfidenceLevel.MEDIUM, ConfidenceLevel.LOW


def test_level_from_codes() -> None:
    assert codes.level_for([]) is H
    assert codes.level_for([codes.CELLS_LABEL_MATCH, "LOW_CONTRAST"]) is H
    assert codes.level_for([vlm.NO_ROLE]) is M
    assert codes.level_for([vlm.NO_ROLE, vlm.CELLS_LABEL_CONFLICT]) is L
    assert codes.level_for([codes.NO_VISUAL_CHANNEL]) is M


def test_unknown_code_uses_severity() -> None:
    assert codes.spec("SOMETHING_ELSE").effect is codes.Effect.CAP_MEDIUM
    assert codes.spec("X", Severity.ERROR).effect is codes.Effect.FORCE_LOW
    assert codes.spec("X", Severity.INFO).effect is codes.Effect.NONE


def test_every_review_code_of_stage_d_has_effect() -> None:
    for code in vlm.REVIEW_CODES:
        assert code in codes.CODES
    assert codes.capping([codes.CELLS_LABEL_MATCH, vlm.NO_ROLE, vlm.NO_ROLE]) == (vlm.NO_ROLE,)


def test_repairable_codes() -> None:
    repairable = {c.code for c in codes.CODES.values() if c.repair is not None}
    assert repairable == {
        vlm.MISSING_IN_TEXT,
        vlm.TYPE_MISMATCH,
        vlm.CELLS_LABEL_CONFLICT,
        codes.LABEL_DISAGREEMENT,
        codes.SOURCE_NOT_FOUND,
        codes.UNMAPPED_ELEMENT,
    }


@pytest.mark.parametrize("position", [0.0, 0.3, 0.5, 0.999, 1.0, -1.0, 2.0])
@pytest.mark.parametrize("level", [H, M, L])
@pytest.mark.parametrize(("high", "low"), [(0.85, 0.5), (0.9, 0.6), (0.7, 0.7)])
def test_banded_value_agrees_with_level(
    level: ConfidenceLevel, position: float, high: float, low: float
) -> None:
    value = banded(level, position, high=high, low=low)
    assert 0.0 <= value <= 1.0
    if high > low:  # pasmo MEDIUM puste, gdy progi są równe
        assert confidence_level(value, high=high, low=low) is level


def test_banded_monotonic_in_position() -> None:
    values = [banded(M, p / 10, high=0.85, low=0.5) for p in range(11)]
    assert values == sorted(values)
    assert banded(H, 0.9, high=0.85, low=0.5) == pytest.approx(0.985)


def test_config_thresholds_from_shared_settings() -> None:
    cfg = load_fusion_config(
        Settings(confidence_high=0.9, confidence_low=0.4), FusionSettings(source_match=85)
    )
    assert (cfg.high, cfg.low, cfg.source_match) == (0.9, 0.4, 85)
    with pytest.raises(ValidationError):
        FusionConfig(high=0.4, low=0.6)
    with pytest.raises(ValidationError):
        FusionConfig(repair_max_iterations=3)
    with pytest.raises(ValidationError):
        FusionConfig(ocr_low=0.95, ocr_high=0.9)


def test_fused_element_is_contract_element() -> None:
    element = FusedElement(
        id="cv-p1-001",
        page=1,
        kind=ElementKind.CHAR_BOXES,
        bbox=BoundingBox(x0=0.1, y0=0.1, x1=0.3, y1=0.12),
        cells=11,
        role=ElementRole.INPUT,
        source=ElementSource.BOTH,
        confidence=0.95,
        decision=Decision.ACCEPTED,
        level=H,
    )
    data = element.model_dump(mode="json")
    assert data["decision"] == "accepted" and data["level"] == "HIGH"
    with pytest.raises(ValidationError):
        FusedElement.model_validate({**data, "id": "vlm-p1-001"})  # vlm-* tylko z source=vlm


def test_field_ref_template() -> None:
    base = {"field_id": uuid4(), "tech_name": "x", "field_type": FieldType.TEXT_FIELD}
    assert FieldRef(**base, origin="standard").from_template
    assert not FieldRef(**base).from_template
    info = FieldRef(**{**base, "field_type": FieldType.TEXT_BLOCK})
    assert info.from_template
    assert FieldRef(**base, title="Imię").label == "Imię"
    assert FieldRef(**base, title="Imię", source_text="Imię i nazwisko").label == "Imię i nazwisko"


def test_repair_problem_key() -> None:
    field_id = uuid4()
    a = RepairProblem(
        code=vlm.CELLS_LABEL_CONFLICT,
        action=codes.RepairAction.CHECK_LENGTH,
        field_id=field_id,
        evidence={"cells": 10, "expected": "11"},
    )
    b = a.model_copy(update={"evidence": {"cells": 9}})
    assert a.key == b.key
    c = RepairProblem(
        code=codes.UNMAPPED_ELEMENT,
        action=codes.RepairAction.ADD_FIELD,
        element_ids=("cv-p1-002",),
    )
    assert c.key == (codes.UNMAPPED_ELEMENT, "ADD_FIELD", "cv-p1-002")
