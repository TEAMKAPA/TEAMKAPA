from uuid import uuid4

import pytest
from pydantic import ValidationError

from backend.shared.config import Settings
from backend.shared.schemas.coi_form import FormDefinition
from backend.shared.schemas.quality import (
    AnalysisResult,
    BoundingBox,
    ConfidenceLevel,
    FieldAssessment,
    Issue,
    QualityReport,
    Severity,
    confidence_level,
)
from tests.coi_helpers import sample

SHA = "a" * 64


@pytest.mark.parametrize(
    ("score", "level"),
    [(1.0, ConfidenceLevel.HIGH), (0.85, ConfidenceLevel.HIGH), (0.84, ConfidenceLevel.MEDIUM),
     (0.5, ConfidenceLevel.MEDIUM), (0.49, ConfidenceLevel.LOW), (0.0, ConfidenceLevel.LOW)],
)  # fmt: skip
def test_confidence_level_thresholds(score: float, level: ConfidenceLevel) -> None:
    assert confidence_level(score, high=0.85, low=0.5) is level


def test_confidence_thresholds_must_be_ordered() -> None:
    with pytest.raises(ValueError, match="low <= high"):
        confidence_level(0.5, high=0.4, low=0.6)


def test_settings_reject_inverted_thresholds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONFIDENCE_HIGH", "0.4")
    monkeypatch.setenv("CONFIDENCE_LOW", "0.6")
    with pytest.raises(ValidationError, match="CONFIDENCE_LOW"):
        Settings()


def test_bbox_must_be_ordered_fractions() -> None:
    BoundingBox(x0=0.1, y0=0.1, x1=0.5, y1=0.2)
    with pytest.raises(ValidationError):
        BoundingBox(x0=0.5, y0=0.1, x1=0.1, y1=0.2)
    with pytest.raises(ValidationError):
        BoundingBox(x0=0.0, y0=0.0, x1=1.5, y1=0.2)


def test_report_counts_and_errors() -> None:
    report = QualityReport(
        document_sha256=SHA,
        accuracy_mode="max",
        fields=[
            FieldAssessment(field_id=uuid4(), tech_name="a", confidence=0.9,
                            level=ConfidenceLevel.HIGH),
            FieldAssessment(field_id=uuid4(), tech_name="b", confidence=0.3,
                            level=ConfidenceLevel.LOW,
                            issues=[Issue(code="X", severity=Severity.ERROR, message="m")]),
        ],
    )  # fmt: skip
    assert report.needs_review == 1
    assert report.has_errors


def test_report_rejects_bad_hash() -> None:
    with pytest.raises(ValidationError):
        QualityReport(document_sha256="xyz", accuracy_mode="max")


def test_analysis_result_keeps_form_in_coi_format() -> None:
    form = FormDefinition.model_validate(sample())
    result = AnalysisResult(
        form=form, report=QualityReport(document_sha256=SHA, accuracy_mode="max")
    )
    dumped = result.model_dump(mode="json", by_alias=True, exclude_none=True)
    assert dumped["form"] == sample()
