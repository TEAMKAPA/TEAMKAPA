"""Etap E.6: sygnały i bramki pewności pola, brak kanału wizualnego, punkt podpięcia."""

import itertools
from typing import Any
from uuid import UUID, uuid5

import pytest

from backend.shared.schemas.coi_form import FieldType
from backend.shared.schemas.quality import (
    BoundingBox,
    ConfidenceLevel,
    FieldAssessment,
    FieldSource,
    Issue,
    Severity,
    SourceChannel,
    confidence_level,
)
from backend.worker.pipeline.contracts import ExtractedDocument, TextElement
from backend.worker.pipeline.normalize import normalize_form
from backend.worker.pipeline.step10_control import control_fields
from backend.worker.vision.fusion import codes
from backend.worker.vision.fusion.adapters import acroform_blocks, field_refs, text_blocks
from backend.worker.vision.fusion.confidence import VisualContext, apply_evidence
from backend.worker.vision.fusion.elements import PageFusionInput, fuse_page
from backend.worker.vision.fusion.models import FieldRef, FusedElement
from backend.worker.vision.models import ElementKind, ElementRole, ElementSource, VisualElement
from backend.worker.vision.vlm import codes as vlm
from backend.worker.vision.vlm.double_read import FormField
from backend.worker.vision.vlm.roles import TextBlock
from tests.coi_helpers import leaf, sample

H, M, L = ConfidenceLevel.HIGH, ConfidenceLevel.MEDIUM, ConfidenceLevel.LOW
NS = UUID("00000000-0000-0000-0000-00000000000e")
LABEL_BOX = BoundingBox(x0=0.05, y0=0.10, x1=0.25, y1=0.12)
LINE_BOX = BoundingBox(x0=0.30, y0=0.115, x1=0.70, y1=0.117)


def blocks(text: str = "Imię i nazwisko", confidence: float | None = None) -> list[TextBlock]:
    return [TextBlock(id="t1", page=1, text=text, bbox=LABEL_BOX, confidence=confidence)]


def element(
    kind: ElementKind = ElementKind.TEXT_LINE,
    role: ElementRole = ElementRole.INPUT,
    *,
    vlm_added: bool = False,
    **extra: Any,
) -> VisualElement:
    if kind is ElementKind.CHAR_BOXES:
        extra.setdefault("cells", 11)
    source = ElementSource.VLM if vlm_added else (
        ElementSource.OPENCV if role is ElementRole.UNKNOWN else ElementSource.BOTH
    )  # fmt: skip
    return VisualElement(
        id="vlm-p1-001" if vlm_added else "cv-p1-001",
        page=1,
        kind=kind,
        bbox=LINE_BOX,
        role=role,
        label_block_id="t1",
        source=source,
        confidence=0.3 if vlm_added else 0.9,
        **extra,
    )


def fused(e: VisualElement, b: list[TextBlock] | None = None) -> tuple[FusedElement, ...]:
    return fuse_page(PageFusionInput(1, (e,), "ok"), b or blocks()).elements


def field(field_type: FieldType = FieldType.TEXT_FIELD, **extra: Any) -> FieldRef:
    extra.setdefault("source_text", "Imię i nazwisko")
    extra.setdefault("text_element_id", "t1")
    return FieldRef(
        field_id=uuid5(NS, "f"), tech_name="imie", field_type=field_type, page=1, **extra
    )


READ_OK = VisualContext(
    pages=frozenset({1}),
    double_read=frozenset({1}),
    vlm_fields=(FormField(id="v1", label="Imię i nazwisko", kind="text", page=1),),
)


def evidence(
    f: FieldRef | None = None,
    e: VisualElement | None = None,
    context: VisualContext = READ_OK,
    b: list[TextBlock] | None = None,
) -> Any:
    f = f or field()
    b = b or blocks()
    elements = fused(e or element(), b)
    return control_fields([f], elements, b, context).evidence[f.field_id]


def team(f: FieldRef | None = None, **extra: Any) -> FieldAssessment:
    values: dict[str, Any] = {
        "field_id": (f or field()).field_id,
        "tech_name": "imie",
        "confidence": 0.8,
        "level": M,
        "signals": {"rules": 1.0, "source_found": 1.0, "source_element": 1.0},
        "sources": [FieldSource(channel=SourceChannel.TEXT, page=1, text="Imię i nazwisko")],
    }
    values.update(extra)
    return FieldAssessment(**values)


def test_all_gates_pass_gives_high() -> None:
    ev = evidence()
    assert (ev.level, ev.reasons) == (H, ())
    assert ev.signals == {"source_page": 1.0, "visual_match": 0.985, "dual_read": 1.0}
    assert ev.element_ids == ("cv-p1-001",)
    out = apply_evidence(team(), ev, high=0.85, low=0.5)
    assert out.level is H and out.confidence >= 0.85
    assert {s.channel for s in out.sources} == {SourceChannel.TEXT, SourceChannel.VISUAL}
    assert out.sources[1].bbox == LINE_BOX


@pytest.mark.parametrize(
    ("e", "level", "code"),
    [
        (element(role=ElementRole.UNKNOWN), M, vlm.NO_ROLE),
        (element(vlm_added=True), L, vlm.VLM_ONLY_ELEMENT),
        (element(ElementKind.CHECKBOX, ElementRole.CHOICE), L, vlm.TYPE_MISMATCH),
    ],
)
def test_element_codes_reach_field(e: VisualElement, level: ConfidenceLevel, code: str) -> None:
    ev = evidence(e=e)
    assert ev.level is level and code in ev.reasons


def test_source_not_found_is_low() -> None:
    ev = evidence(f=field(source_text="Adres zamieszkania"))
    assert ev.level is L and codes.SOURCE_NOT_FOUND in ev.reasons
    assert ev.signals["source_page"] < 0.9


def test_cells_conflict_with_validator_is_low() -> None:
    boxes = element(ElementKind.CHAR_BOXES, cells=10)
    ev = evidence(f=field(validators=("PESEL",)), e=boxes)
    assert ev.level is L and vlm.CELLS_LABEL_CONFLICT in ev.reasons
    ok = evidence(f=field(validators=("PESEL",)), e=element(ElementKind.CHAR_BOXES, cells=11))
    assert ok.level is H


@pytest.mark.parametrize("cause", ["OFFICE_FORMAT_NOT_RENDERED", "PDF_UNREADABLE"])
def test_no_visual_channel_caps_at_medium(cause: str) -> None:
    ev = evidence(context=VisualContext.unavailable(cause))
    assert ev.level is M and ev.reasons == (codes.NO_VISUAL_CHANNEL,)
    (issue,) = ev.issues
    assert cause in issue.message and issue.severity is Severity.WARNING
    # Nawet ocena zespołu z najwyższą pewnością zostaje najwyżej MEDIUM.
    out = apply_evidence(team(confidence=1.0, level=H), ev, high=0.85, low=0.5)
    assert out.level is M and out.confidence < 0.85


def test_page_outside_visual_channel_is_no_visual_channel() -> None:
    ev = evidence(context=VisualContext(pages=frozenset({2})))
    assert codes.NO_VISUAL_CHANNEL in ev.reasons and "strona" in ev.issues[0].message


def test_dual_read_missing_and_type_mismatch() -> None:
    missing = VisualContext(pages=frozenset({1}), double_read=frozenset({1}))
    ev = evidence(context=missing)
    assert ev.level is M and vlm.MISSING_IN_VLM in ev.reasons and ev.signals["dual_read"] == 0.3
    choice = VisualContext(
        pages=frozenset({1}),
        double_read=frozenset({1}),
        vlm_fields=(FormField(id="v", label="Imię i nazwisko", kind="choice", page=1),),
    )
    ev = evidence(context=choice)
    assert ev.level is L and vlm.TYPE_MISMATCH in ev.reasons


def test_balanced_skip_allows_high_but_failed_read_does_not() -> None:
    skipped = VisualContext(
        pages=frozenset({1}), mode="balanced", double_read_skipped=frozenset({1})
    )
    ev = evidence(context=skipped)
    assert ev.level is H and codes.DUAL_READ_SKIPPED in ev.reasons
    failed = VisualContext(pages=frozenset({1}), mode="max")
    ev = evidence(context=failed)
    assert ev.level is M and codes.DUAL_READ_UNAVAILABLE in ev.reasons


@pytest.mark.parametrize(
    ("ocr", "vlm_label", "level", "code"),
    [
        (0.95, "Imię i nazwisko", H, None),
        (0.75, "Imię i nazwisko", M, codes.OCR_UNCERTAIN),
        (0.5, "Imię i nazwisko", L, vlm.LOW_OCR_CONFIDENCE),
        (0.95, None, M, vlm.MISSING_IN_VLM),
    ],
)
def test_scan_label_and_ocr(
    ocr: float, vlm_label: str | None, level: ConfidenceLevel, code: str | None
) -> None:
    b = blocks(confidence=ocr)
    fields = (FormField(id="v", label=vlm_label, kind="text", page=1),) if vlm_label else ()
    context = VisualContext(pages=frozenset({1}), double_read=frozenset({1}), vlm_fields=fields)
    ev = evidence(context=context, b=b)
    assert ev.level is level and ev.signals["ocr"] == ocr
    if code:
        assert code in ev.reasons
    if vlm_label is None:
        assert codes.LABEL_SINGLE_READ in ev.reasons  # skan z jednym odczytem etykiety


def test_template_field_has_no_visual_gates() -> None:
    f = field(origin="standard")
    ev = evidence(f=f)
    assert (ev.level, ev.reasons, ev.signals) == (H, (), {})
    t = team(f, level=H, confidence=0.9)
    assert apply_evidence(t, ev, high=0.85, low=0.5).level is H


def test_apply_evidence_respects_team_issues_and_demo_mode() -> None:
    ev = evidence()
    error = Issue(code="FINISH_TAB_LAST", severity=Severity.ERROR, message="x")
    warning = Issue(code="DOMAIN_RULE_OVERRIDE", severity=Severity.WARNING, message="x")
    assert apply_evidence(team(issues=[error]), ev, high=0.85, low=0.5).level is L
    assert apply_evidence(team(issues=[warning]), ev, high=0.85, low=0.5).level is M
    assert apply_evidence(team(), ev, high=0.85, low=0.5, demo_mode=True).level is L
    assert apply_evidence(team(), None, high=0.85, low=0.5) == team()
    other = ev.model_copy(update={"field_id": uuid5(NS, "inne")})
    with pytest.raises(ValueError, match="innego pola"):
        apply_evidence(team(), other, high=0.85, low=0.5)


def test_apply_evidence_does_not_duplicate_codes_or_sources() -> None:
    ev = evidence(f=field(source_text="Adres zamieszkania"))
    t = team(issues=[Issue(code="SOURCE_NOT_FOUND", severity=Severity.WARNING, message="x")])
    out = apply_evidence(t, ev, high=0.85, low=0.5)
    assert [i.code for i in out.issues].count("SOURCE_NOT_FOUND") == 1
    again = apply_evidence(out, ev, high=0.85, low=0.5)
    assert len(again.sources) == len(out.sources)


def test_level_and_number_always_agree() -> None:
    """Dla kombinacji elementów i kontekstów: poziom = bramka, liczba w paśmie poziomu."""
    elements = [
        element(),
        element(role=ElementRole.UNKNOWN),
        element(vlm_added=True),
        element(ElementKind.CHAR_BOXES, cells=10),
    ]
    contexts = [READ_OK, VisualContext(pages=frozenset({1})), VisualContext.unavailable("X")]
    for e, context in itertools.product(elements, contexts):
        ev = evidence(e=e, context=context)
        assert ev.level is codes.level_for(ev.reasons)
        out = apply_evidence(team(), ev, high=0.85, low=0.5)
        assert confidence_level(out.confidence, high=0.85, low=0.5) is out.level
        assert out.level is not H or not codes.capping(ev.reasons)


def test_adapters_keep_text_ids_and_sources() -> None:
    raw = sample()
    target = leaf(raw, 1, 0, 1)
    target.update({"source_text": "Miejscowość", "page": 2, "element_id": "txt-9"})
    refs = {str(r.field_id): r for r in field_refs(normalize_form(raw))}
    ref = refs[target["id"]]
    assert (ref.source_text, ref.page, ref.text_element_id) == ("Miejscowość", 2, "txt-9")
    extracted = ExtractedDocument(
        page_count=1,
        elements=[
            TextElement(element_id="txt-9", page=1, text="Miejscowość", bbox=LABEL_BOX),
            TextElement(element_id="txt-10", page=1, text="  "),
            TextElement(element_id="acro-1", page=1, text="", channel=SourceChannel.ACROFORM),
        ],
    )
    assert [b.id for b in text_blocks(extracted)] == ["txt-9"]
    assert [b.id for b in acroform_blocks(extracted)] == ["acro-1"]
