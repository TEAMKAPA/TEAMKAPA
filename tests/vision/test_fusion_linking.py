"""Etap E.5: łączenie pól z elementami, kompletność i źródła w tekście strony."""

from typing import Any
from uuid import UUID, uuid5

from backend.shared.schemas.coi_form import FieldType
from backend.shared.schemas.quality import BoundingBox
from backend.worker.vision.fusion.elements import PageFusionInput, fuse_page
from backend.worker.vision.fusion.linking import (
    SKIP_OFFICE,
    SKIP_ROLE,
    SKIP_SIGNATURE,
    link_fields,
    page_size_mm,
    visual_expected,
)
from backend.worker.vision.fusion.models import FieldRef, FusedElement
from backend.worker.vision.models import ElementKind, ElementRole, ElementSource, VisualElement
from backend.worker.vision.vlm.roles import TextBlock

NS = UUID("00000000-0000-0000-0000-000000000009")


def box(x0: float, y0: float, x1: float, y1: float) -> BoundingBox:
    return BoundingBox(x0=x0, y0=y0, x1=x1, y1=y1)


def el(
    number: int,
    kind: ElementKind,
    bbox: BoundingBox,
    role: ElementRole = ElementRole.INPUT,
    page: int = 1,
    **extra: Any,
) -> VisualElement:
    if kind is ElementKind.CHAR_BOXES:
        extra.setdefault("cells", 5)
    return VisualElement(
        id=f"cv-p{page}-{number:03d}",
        page=page,
        kind=kind,
        bbox=bbox,
        role=role,
        source=ElementSource.OPENCV if role is ElementRole.UNKNOWN else ElementSource.BOTH,
        confidence=0.9,
        **extra,
    )


def fused(*elements: VisualElement, page: int = 1) -> tuple[FusedElement, ...]:
    return fuse_page(PageFusionInput(page, elements, "ok"), []).elements


def field(name: str, field_type: FieldType = FieldType.TEXT_FIELD, **extra: Any) -> FieldRef:
    extra.setdefault("page", 1)
    return FieldRef(
        field_id=uuid5(NS, name), tech_name=name, title=name, field_type=field_type, **extra
    )


def block(identifier: str, text: str, bbox: BoundingBox, page: int = 1) -> TextBlock:
    return TextBlock(id=identifier, page=page, text=text, bbox=bbox)


BLOCKS = [
    block("p1-b1", "Imię i nazwisko", box(0.05, 0.10, 0.25, 0.12)),
    block("p1-b2", "Stan cywilny", box(0.05, 0.20, 0.20, 0.22)),
    block("p1-b3", "Numer telefonu", box(0.05, 0.30, 0.22, 0.32)),
    block("p1-b4", "NIP", box(0.05, 0.40, 0.09, 0.42)),
]


def test_exact_link_by_label_block() -> None:
    elements = fused(
        el(1, ElementKind.TEXT_LINE, box(0.3, 0.115, 0.7, 0.117), label_block_id="p1-b1")
    )
    f = field("imie", source_text="Imię i nazwisko", text_element_id="p1-b1")
    result = link_fields([f], elements, BLOCKS)
    link = result.links[f.field_id]
    assert (link.element.id, link.method, link.kind_ok) == ("cv-p1-001", "label_block", True)
    assert result.unmapped == ()


def test_exact_link_with_incompatible_kind() -> None:
    elements = fused(
        el(
            1,
            ElementKind.CHECKBOX,
            box(0.3, 0.10, 0.32, 0.115),
            ElementRole.CHOICE,
            label_block_id="p1-b1",
        )
    )
    f = field("imie", source_text="Imię i nazwisko", text_element_id="p1-b1")
    link = link_fields([f], elements, BLOCKS).links[f.field_id]
    assert not link.kind_ok


def test_choice_field_prefers_group_and_maps_members() -> None:
    c1 = el(
        1,
        ElementKind.CHECKBOX,
        box(0.30, 0.20, 0.32, 0.215),
        ElementRole.CHOICE,
        label_block_id="p1-b2",
    )
    c2 = el(2, ElementKind.CHECKBOX, box(0.40, 0.20, 0.42, 0.215), ElementRole.CHOICE)
    group = el(3, ElementKind.CHECKBOX_GROUP, box(0.30, 0.20, 0.42, 0.215), ElementRole.CHOICE,
               options=("cv-p1-001", "cv-p1-002"), multi=False, label_block_id="p1-b2")  # fmt: skip
    f = field("stan", FieldType.RADIO, source_text="Stan cywilny", text_element_id="p1-b2")
    result = link_fields([f], fused(c1, c2, group), BLOCKS)
    assert result.links[f.field_id].element.id == "cv-p1-003"
    assert result.unmapped == ()


def test_proximity_link_right_of_label() -> None:
    near = el(1, ElementKind.DOTTED_LINE, box(0.25, 0.315, 0.6, 0.317))
    f = field("tel", source_text="Numer telefonu", text_element_id="p1-b3")
    link = link_fields([f], fused(near), BLOCKS).links[f.field_id]
    assert (link.element.id, link.method) == ("cv-p1-001", "proximity")
    far = el(1, ElementKind.DOTTED_LINE, box(0.5, 0.315, 0.9, 0.317))  # ok. 59 mm od etykiety
    assert f.field_id not in link_fields([f], fused(far), BLOCKS).links


def test_proximity_below_label_and_foreign_label_not_taken() -> None:
    below = el(1, ElementKind.TEXT_LINE, box(0.05, 0.34, 0.6, 0.342))
    f = field("tel", source_text="Numer telefonu", text_element_id="p1-b3")
    assert link_fields([f], fused(below), BLOCKS).links[f.field_id].element.id == "cv-p1-001"
    owned = el(1, ElementKind.TEXT_LINE, box(0.05, 0.34, 0.6, 0.342), label_block_id="p1-b4")
    other = field("nip", source_text="NIP", text_element_id="p1-b4")
    result = link_fields([f, other], fused(owned), BLOCKS)
    assert f.field_id not in result.links
    assert result.links[other.field_id].element.id == "cv-p1-001"


def test_template_and_attachments_are_not_linked() -> None:
    template = field("email", origin="standard")
    upload = field("zalacznik", FieldType.FILE_UPLOADER, source_text="Załącznik")
    assert not visual_expected(template) and not visual_expected(upload)
    result = link_fields([template, upload], (), BLOCKS)
    assert result.links == {}
    assert template.field_id not in result.sources
    assert upload.field_id in result.sources


def test_source_check_fuzzy_and_page_scoped() -> None:
    ocr = [block("p1-b1", "Imie i nazwlsko wnioskodawcy", box(0.05, 0.1, 0.4, 0.12))]
    found = field("a", source_text="Imię i nazwisko", text_element_id="p1-b1")
    missing = field("b", source_text="Adres zamieszkania")
    other_page = field("c", source_text="Imię i nazwisko", page=2)
    result = link_fields([found, missing, other_page], (), ocr)
    assert result.sources[found.field_id].found and result.sources[found.field_id].block_found
    assert not result.sources[missing.field_id].found
    assert not result.sources[other_page.field_id].found


def test_short_label_must_be_whole_word() -> None:
    text = [block("p1-b1", "Numer NIPPON", box(0.05, 0.1, 0.4, 0.12))]
    assert (
        not link_fields([field("nip", source_text="NIP")], (), text).sources[uuid5(NS, "nip")].found
    )
    assert (
        link_fields([field("nip", source_text="NIP")], (), BLOCKS).sources[uuid5(NS, "nip")].found
    )


def test_completeness_skipped_and_unmapped() -> None:
    elements = fused(
        el(1, ElementKind.TEXT_LINE, box(0.3, 0.5, 0.6, 0.502)),  # do wypełnienia, bez pola
        el(2, ElementKind.TEXT_LINE, box(0.3, 0.55, 0.6, 0.552), ElementRole.UNKNOWN),
        el(3, ElementKind.OFFICE_ONLY, box(0.3, 0.6, 0.6, 0.65), ElementRole.SKIP),
        el(4, ElementKind.TABLE_CELL, box(0.3, 0.7, 0.6, 0.75), ElementRole.SKIP),
        el(5, ElementKind.DOTTED_LINE, box(0.3, 0.8, 0.6, 0.802), ElementRole.E_SIGNATURE),
        el(6, ElementKind.SECTION_FRAME, box(0.0, 0.0, 1.0, 1.0), ElementRole.SECTION),
    )
    acro = [block("acro-1", "pole", box(0.1, 0.9, 0.2, 0.92))]
    result = link_fields([], elements, BLOCKS, acroform=acro)
    assert [(u.element_id, u.role_known, u.acroform) for u in result.unmapped] == [
        ("cv-p1-001", True, False),
        ("cv-p1-002", False, False),
        ("acro-1", True, True),
    ]
    reasons = {s.source.element_id: s.reason for s in result.skipped}
    assert reasons == {
        "cv-p1-003": SKIP_OFFICE,
        "cv-p1-004": SKIP_ROLE,
        "cv-p1-005": SKIP_SIGNATURE,
    }


def test_acroform_referenced_by_field_is_mapped() -> None:
    acro = [block("acro-1", "pole", box(0.1, 0.9, 0.2, 0.92))]
    f = field("p", source_text="pole", text_element_id="acro-1")
    assert link_fields([f], (), BLOCKS, acroform=acro).unmapped == ()


def test_page_size_mm() -> None:
    assert page_size_mm(1654, 2339, 200) == (1654 / 200 * 25.4, 2339 / 200 * 25.4)
    assert page_size_mm(2339, 1654, None) == (297.0, 210.0)
