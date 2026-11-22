"""Etap E.2: fuzja elementów strony według tabeli 7.6 briefu."""

from typing import Any

import pytest

from backend.shared.schemas.quality import BoundingBox, ConfidenceLevel, Severity
from backend.worker.pipeline.step04_fusion import fuse
from backend.worker.vision.errors import IssueCode
from backend.worker.vision.fusion import codes
from backend.worker.vision.fusion.elements import (
    PageFusionInput,
    Verdict,
    fuse_page,
    page_codes_from_meta,
)
from backend.worker.vision.fusion.models import Decision
from backend.worker.vision.models import (
    DpiSource,
    ElementKind,
    ElementRole,
    ElementSource,
    PageMeta,
    PageSource,
    RenderIssue,
    VisualElement,
)
from backend.worker.vision.vlm import codes as vlm
from backend.worker.vision.vlm.roles import TextBlock

H, M, L = ConfidenceLevel.HIGH, ConfidenceLevel.MEDIUM, ConfidenceLevel.LOW


def element(
    number: int,
    kind: ElementKind = ElementKind.TEXT_LINE,
    role: ElementRole = ElementRole.INPUT,
    *,
    vlm_added: bool = False,
    confidence: float = 0.9,
    y: float = 0.1,
    **extra: Any,
) -> VisualElement:
    if kind is ElementKind.CHAR_BOXES:
        extra.setdefault("cells", 11)
    source = (
        ElementSource.VLM
        if vlm_added
        else (ElementSource.BOTH if role is not ElementRole.UNKNOWN else ElementSource.OPENCV)
    )
    return VisualElement(
        id=f"{'vlm' if vlm_added else 'cv'}-p1-{number:03d}",
        page=1,
        kind=kind,
        bbox=BoundingBox(x0=0.3, y0=y, x1=0.6, y1=y + 0.02),
        role=role,
        source=source,
        confidence=confidence,
        **extra,
    )


def block(identifier: str, text: str) -> TextBlock:
    return TextBlock(
        id=identifier, page=1, text=text, bbox=BoundingBox(x0=0.05, y0=0.1, x1=0.25, y1=0.12)
    )


def test_agreement_is_accepted_high() -> None:
    result = fuse_page(PageFusionInput(1, (element(1),), "ok"), [])
    (fused,) = result.elements
    assert (fused.decision, fused.level, fused.confidence) == (Decision.ACCEPTED, H, 0.985)
    assert fused.confidence_reasons == ()
    assert result.rejected == () and result.review == ()


def test_no_role_is_review_medium() -> None:
    (fused,) = fuse_page(
        PageFusionInput(1, (element(1, role=ElementRole.UNKNOWN),), "ok"), []
    ).elements
    assert (fused.decision, fused.level) == (Decision.REVIEW, M)
    assert vlm.NO_ROLE in fused.confidence_reasons
    assert 0.5 <= fused.confidence < 0.85


@pytest.mark.parametrize(
    ("status", "code"),
    [
        ("unavailable", vlm.VLM_UNAVAILABLE),
        ("not_run", vlm.VLM_UNAVAILABLE),
        ("invalid", vlm.VLM_INVALID_RESPONSE),
    ],
)
def test_vlm_failure_caps_page_at_medium(status: str, code: str) -> None:
    elements = (
        element(1, role=ElementRole.UNKNOWN),
        element(2, ElementKind.CHECKBOX, role=ElementRole.UNKNOWN),
    )
    result = fuse_page(PageFusionInput(1, elements, status), [])  # type: ignore[arg-type]
    assert {e.level for e in result.elements} == {M}
    assert all(code in e.confidence_reasons for e in result.elements)
    assert all(vlm.NO_ROLE not in e.confidence_reasons for e in result.elements)


def test_vlm_added_is_low_and_kept() -> None:
    added = element(1, ElementKind.DOTTED_LINE, vlm_added=True, confidence=0.3)
    (fused,) = fuse_page(PageFusionInput(1, (added,), "ok"), []).elements
    assert (fused.decision, fused.level) == (Decision.ADDED, L)
    assert fused.confidence_reasons[0] == vlm.VLM_ONLY_ELEMENT
    assert fused.confidence == pytest.approx(0.3)


def test_vlm_added_with_evidence_codes() -> None:
    added = element(1, ElementKind.DOTTED_LINE, vlm_added=True, confidence=0.3)
    weak = fuse_page(
        PageFusionInput(
            1, (added,), "ok", verify=lambda _: Verdict(True, codes.WEAK_VISUAL_EVIDENCE, "DOTS")
        ),
        [],
    ).elements[0]
    blank = fuse_page(
        PageFusionInput(
            1, (added,), "ok", verify=lambda _: Verdict(True, codes.NO_VISUAL_EVIDENCE, "BLANK")
        ),
        [],
    ).elements[0]
    assert weak.level is blank.level is L
    assert codes.WEAK_VISUAL_EVIDENCE in weak.confidence_reasons
    assert weak.confidence > blank.confidence


def test_vlm_added_contradicted_by_image_is_removed_but_reported() -> None:
    added = element(2, ElementKind.DOTTED_LINE, vlm_added=True, confidence=0.3)
    reject = Verdict(False, codes.VLM_ONLY_REJECTED, "TEXT_ROW", "tekst 0.8 szerokości")
    result = fuse_page(PageFusionInput(1, (element(1), added), "ok", verify=lambda _: reject), [])
    assert [e.id for e in result.elements] == ["cv-p1-001"]
    (rejected,) = result.rejected
    assert (rejected.element.id, rejected.reason) == ("vlm-p1-002", "TEXT_ROW")
    (item,) = result.review
    assert (item.code, item.element_id) == (codes.VLM_ONLY_REJECTED, "vlm-p1-002")
    assert "TEXT_ROW" in item.detail


def test_cells_agree_with_label() -> None:
    boxes = element(1, ElementKind.CHAR_BOXES, label_block_id="p1-b1", cells=11)
    (fused,) = fuse_page(
        PageFusionInput(1, (boxes,), "ok"), [block("p1-b1", "Numer PESEL")]
    ).elements
    assert fused.level is H
    assert codes.CELLS_LABEL_MATCH in fused.confidence_reasons


def test_cells_conflict_with_label_is_low() -> None:
    boxes = element(1, ElementKind.CHAR_BOXES, label_block_id="p1-b1", cells=10)
    result = fuse_page(PageFusionInput(1, (boxes,), "ok"), [block("p1-b1", "PESEL")])
    (fused,) = result.elements
    assert (fused.level, fused.decision) == (L, Decision.REVIEW)
    assert vlm.CELLS_LABEL_CONFLICT in fused.confidence_reasons
    assert [r.code for r in result.review] == [vlm.CELLS_LABEL_CONFLICT]
    # Konflikt zgłoszony już przez etap VLM nie jest dublowany w przeglądzie.
    reported = boxes.model_copy(update={"confidence_reasons": (vlm.CELLS_LABEL_CONFLICT,)})
    again = fuse_page(PageFusionInput(1, (reported,), "ok"), [block("p1-b1", "PESEL")])
    assert again.review == ()
    assert again.elements[0].confidence_reasons.count(vlm.CELLS_LABEL_CONFLICT) == 1


def test_cells_without_known_pattern_are_neutral() -> None:
    boxes = element(1, ElementKind.CHAR_BOXES, label_block_id="p1-b1", cells=7)
    (fused,) = fuse_page(
        PageFusionInput(1, (boxes,), "ok"), [block("p1-b1", "Numer domu")]
    ).elements
    assert fused.level is H and fused.confidence_reasons == ()


def test_group_with_unknown_multi_is_medium() -> None:
    box1 = element(1, ElementKind.CHECKBOX, ElementRole.CHOICE)
    box2 = element(2, ElementKind.CHECKBOX, ElementRole.CHOICE)
    group = element(
        3,
        ElementKind.CHECKBOX_GROUP,
        ElementRole.CHOICE,
        confidence=0.3,
        options=("cv-p1-001", "cv-p1-002"),
        confidence_reasons=(codes.GROUP_CANDIDATE,),
    )
    result = fuse_page(PageFusionInput(1, (box1, box2, group), "ok"), [])
    levels = {e.id: e.level for e in result.elements}
    assert levels == {"cv-p1-001": H, "cv-p1-002": H, "cv-p1-003": M}
    assert codes.GROUP_MULTI_UNKNOWN in result.elements[2].confidence_reasons
    known = group.model_copy(update={"multi": False, "confidence_reasons": ()})
    assert fuse_page(PageFusionInput(1, (box1, box2, known), "ok"), []).elements[2].level is H


def test_page_codes_cap_every_element() -> None:
    result = fuse_page(
        PageFusionInput(1, (element(1), element(2, ElementKind.CHECKBOX, ElementRole.CHOICE)), "ok",
                        page_codes=(IssueCode.DPI_REDUCED.value,)),
        [],
    )  # fmt: skip
    assert {e.level for e in result.elements} == {M}
    assert all("DPI_REDUCED" in e.confidence_reasons for e in result.elements)


def test_page_codes_from_meta() -> None:
    def meta(*issues: RenderIssue) -> PageMeta:
        return PageMeta(
            page=1, width_px=100, height_px=141, dpi=None, dpi_source=DpiSource.UNKNOWN,
            source=PageSource.IMAGE, issues=issues,
        )  # fmt: skip

    info = RenderIssue(code=IssueCode.DPI_UNKNOWN, severity=Severity.INFO)
    warning = RenderIssue(code=IssueCode.DPI_UNKNOWN, severity=Severity.WARNING)
    tall = RenderIssue(code=IssueCode.TALL_IMAGE_UNSPLIT, severity=Severity.WARNING)
    split = RenderIssue(code=IssueCode.IMAGE_SPLIT_A4, severity=Severity.INFO)
    assert page_codes_from_meta(meta(info, split)) == ()
    assert page_codes_from_meta(meta(warning, tall, tall)) == ("DPI_UNKNOWN", "TALL_IMAGE_UNSPLIT")


def test_low_contrast_does_not_cap_but_keeps_detector_factor() -> None:
    low = element(1, confidence=0.72, confidence_reasons=("LOW_CONTRAST",))
    (fused,) = fuse_page(PageFusionInput(1, (low,), "ok"), []).elements
    assert fused.level is H
    assert (
        fused.confidence
        < fuse_page(PageFusionInput(1, (element(1),), "ok"), []).elements[0].confidence
    )


def test_wrong_page_is_error() -> None:
    with pytest.raises(ValueError, match="nie należy"):
        fuse_page(PageFusionInput(2, (element(1),), "ok"), [])


def test_document_order_and_determinism() -> None:
    page1 = PageFusionInput(1, (element(1), element(2, role=ElementRole.UNKNOWN, y=0.3)), "ok")
    page2 = PageFusionInput(
        2,
        (element(1).model_copy(update={"id": "cv-p2-001", "page": 2}),),
        "unavailable",
    )
    first = fuse([page2, page1], [])
    second = fuse([page1, page2], [])
    assert [p.page for p in first] == [1, 2]
    assert first == second
    assert [e.id for e in first[0].elements] == ["cv-p1-001", "cv-p1-002"]
