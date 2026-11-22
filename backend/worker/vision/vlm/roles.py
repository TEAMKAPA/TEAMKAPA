"""Tryb 1 (#11): role i etykiety elementów ``cv-*`` oraz ograniczone dodawanie ``vlm-*``.

VLM dostaje obraz strony z numerami elementów, listę elementów OpenCV (ID, kategoria, bbox)
i bloki tekstu (ID, tekst, bbox). Zwraca rolę i ID bloku etykiety dla każdego elementu.

Walidacja deterministyczna odpowiedzi:

- VLM **nie zmienia bbox ani kategorii** elementów OpenCV; ID spoza listy są odrzucane,
- etykieta to wyłącznie ID istniejącego bloku tekstu (tekst etykiety pochodzi ze źródła, nie z VLM),
- rola niezgodna z kategorią (np. ``choice`` dla komórki) jest odrzucana; brak roli → ``NO_ROLE``,
- element z rolą od VLM ma ``source=both``; bez roli zostaje ``opencv``,
- dodane elementy: najwyżej ``max_added_per_page``, poprawny bbox, rola do wypełnienia (``input``,
  ``choice``, ``e_signature``), nie powtarzają elementu OpenCV (IoU ramek rozszerzonych pionowo do
  1% strony ≥ 0,3 albo środek w ramce), ID ``vlm-p{n}-{nnn}``, pewność 0,3, ``VLM_ONLY_ELEMENT``,
- komórka zawierająca inny element do wypełnienia dostaje rolę ``skip`` (kontener, LABELING.md),
- błąd API (``VLM_UNAVAILABLE``) albo odpowiedź niezgodna ze schematem po ponowieniu
  (``VLM_INVALID_RESPONSE``): elementy bez zmian, z kodem powodu; pipeline działa dalej.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

from backend.shared.schemas.quality import BoundingBox
from backend.worker.vision.models import (
    ElementKind,
    ElementRole,
    ElementSource,
    ImageArray,
    VisualElement,
    has_area,
)
from backend.worker.vision.vlm.client import VlmClient, VlmError, VlmUsage
from backend.worker.vision.vlm.codes import (
    LOW_OCR_CONFIDENCE,
    NO_ROLE,
    VLM_INVALID_RESPONSE,
    VLM_ONLY_ELEMENT,
    ReviewItem,
)
from backend.worker.vision.vlm.config import VlmSettings
from backend.worker.vision.vlm.image import page_image
from backend.worker.vision.vlm.prompts import data_lines, load_prompt
from backend.worker.vision.vlm.schemas import AddedElement, RolesResponse
from backend.worker.vision.vlm.structured import ask

TASK = "roles"
ADDED_CONFIDENCE = 0.3
DUPLICATE_IOU = 0.3
MIN_LINE_HEIGHT = 0.01  # ułamek wysokości strony (jak w ewaluacji #12)
CONTAIN_FRACTION = 0.9
FILLABLE_ROLES = frozenset({ElementRole.INPUT, ElementRole.CHOICE, ElementRole.E_SIGNATURE})
MAX_BLOCK_TEXT = 200

FILL = frozenset({ElementRole.INPUT, ElementRole.SKIP, ElementRole.E_SIGNATURE})
ROLES_FOR_KIND: dict[ElementKind, frozenset[ElementRole]] = {
    ElementKind.CHAR_BOXES: frozenset({ElementRole.INPUT, ElementRole.SKIP}),
    ElementKind.CHECKBOX: frozenset({ElementRole.CHOICE, ElementRole.SKIP}),
    ElementKind.CHECKBOX_GROUP: frozenset({ElementRole.CHOICE, ElementRole.SKIP}),
    ElementKind.TEXT_LINE: FILL,
    ElementKind.DOTTED_LINE: FILL,
    ElementKind.TABLE_CELL: FILL,
    ElementKind.SECTION_FRAME: frozenset({ElementRole.SECTION, ElementRole.SKIP}),
    ElementKind.SIGNATURE: frozenset({ElementRole.E_SIGNATURE, ElementRole.SKIP}),
    ElementKind.OFFICE_ONLY: frozenset({ElementRole.SKIP}),
}
"""Role dopuszczalne dla kategorii elementu (linia lub komórka może być miejscem na podpis)."""

Status = Literal["ok", "invalid", "unavailable"]


@dataclass(frozen=True)
class TextBlock:
    """Blok tekstu strony z kanału tekstowego (#7: Docling/OCR); źródło etykiet."""

    id: str
    page: int
    text: str
    bbox: BoundingBox | None = None
    confidence: float | None = None  # pewność OCR; None dla warstwy tekstowej


@dataclass(frozen=True)
class RolesResult:
    elements: tuple[VisualElement, ...]
    review: tuple[ReviewItem, ...]
    status: Status
    attempts: int = 0
    usage: VlmUsage = field(default_factory=VlmUsage)
    cached: bool = False


def _box(b: BoundingBox) -> list[float]:
    return [round(b.x0, 3), round(b.y0, 3), round(b.x1, 3), round(b.y1, 3)]


def prompt_values(
    page: int, elements: Sequence[VisualElement], blocks: Sequence[TextBlock], settings: VlmSettings
) -> dict[str, str]:
    element_rows: list[dict[str, object]] = [
        {"id": e.id, "kind": e.kind.value, "bbox": _box(e.bbox), "cells": e.cells} for e in elements
    ]
    block_rows: list[dict[str, object]] = [
        {"id": b.id, "text": b.text[:MAX_BLOCK_TEXT], "bbox": _box(b.bbox) if b.bbox else None}
        for b in blocks
    ]
    return {
        "PAGE": str(page),
        "MAX_ADDED": str(settings.max_added_per_page),
        "ELEMENTS": data_lines(element_rows),
        "TEXT_BLOCKS": data_lines(block_rows),
    }


def _iou(a: BoundingBox, b: BoundingBox) -> float:
    w = min(a.x1, b.x1) - max(a.x0, b.x0)
    h = min(a.y1, b.y1) - max(a.y0, b.y0)
    if w <= 0 or h <= 0:
        return 0.0
    inter = w * h
    union = (a.x1 - a.x0) * (a.y1 - a.y0) + (b.x1 - b.x0) * (b.y1 - b.y0) - inter
    return inter / union if union > 0 else 0.0


def _expanded(b: BoundingBox) -> BoundingBox:
    """Ramka rozszerzona pionowo do ``MIN_LINE_HEIGHT`` (cienkie linie, niedokładny bbox VLM)."""
    if b.y1 - b.y0 >= MIN_LINE_HEIGHT:
        return b
    center, half = (b.y0 + b.y1) / 2, MIN_LINE_HEIGHT / 2
    return BoundingBox(x0=b.x0, y0=max(0.0, center - half), x1=b.x1, y1=min(1.0, center + half))


def _duplicate(box: BoundingBox, other: BoundingBox) -> bool:
    """Czy dodany element powtarza istniejący: IoU ramek rozszerzonych albo środek w ramce."""
    a, b = _expanded(box), _expanded(other)
    if _iou(a, b) >= DUPLICATE_IOU:
        return True
    cx, cy = (box.x0 + box.x1) / 2, (box.y0 + box.y1) / 2
    return b.x0 <= cx <= b.x1 and b.y0 <= cy <= b.y1


def _contains(outer: BoundingBox, inner: BoundingBox) -> bool:
    w = min(outer.x1, inner.x1) - max(outer.x0, inner.x0)
    h = min(outer.y1, inner.y1) - max(outer.y0, inner.y0)
    area = (inner.x1 - inner.x0) * (inner.y1 - inner.y0)
    smaller = area < (outer.x1 - outer.x0) * (outer.y1 - outer.y0)
    return w > 0 and h > 0 and smaller and w * h >= CONTAIN_FRACTION * area


def container_cells_skip(elements: Sequence[VisualElement]) -> list[VisualElement]:
    """Komórka zawierająca inny element do wypełnienia dostaje rolę ``skip`` (LABELING.md).

    Polem jest element wewnątrz (kropki, kratki, checkbox), a komórka jest tylko kontenerem; ta sama
    reguła tworzy etykiety zbioru #12 (``eval/visual/convert_ls.py``).
    """
    fillable = [
        e for e in elements if e.role in FILLABLE_ROLES and e.kind is not ElementKind.SECTION_FRAME
    ]
    result = []
    for element in elements:
        if (
            element.kind is ElementKind.TABLE_CELL
            and element.role is ElementRole.INPUT
            and any(o is not element and _contains(element.bbox, o.bbox) for o in fillable)
        ):
            element = element.model_copy(update={"role": ElementRole.SKIP})
        result.append(element)
    return result


def _with_reason(element: VisualElement, code: str) -> VisualElement:
    if code in element.confidence_reasons:
        return element
    return element.model_copy(update={"confidence_reasons": (*element.confidence_reasons, code)})


def _failed(page: int, elements: Sequence[VisualElement], error: VlmError) -> RolesResult:
    marked = tuple(_with_reason(e, error.code) for e in elements)
    item = ReviewItem(code=error.code, page=page, detail=f"tryb 1: {error.detail}")
    status: Status = "invalid" if error.code == VLM_INVALID_RESPONSE else "unavailable"
    return RolesResult(elements=marked, review=(item,), status=status)


def _added(
    page: int,
    answers: Sequence[AddedElement],
    existing: Sequence[VisualElement],
    block_ids: set[str],
    settings: VlmSettings,
) -> tuple[list[VisualElement], list[ReviewItem]]:
    accepted: list[tuple[BoundingBox, AddedElement]] = []
    review: list[ReviewItem] = []
    for answer in answers:
        x0, y0, x1, y1 = answer.bbox
        if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
            review.append(
                ReviewItem(code=VLM_INVALID_RESPONSE, page=page, detail="dodany element: zły bbox")
            )
            continue
        box = BoundingBox(x0=x0, y0=y0, x1=x1, y1=y1)
        if ElementRole(answer.role) not in FILLABLE_ROLES:
            continue  # dodany element ma sens tylko jako miejsce do wypełnienia
        if any(_duplicate(box, e.bbox) for e in existing) or any(
            _duplicate(box, other) for other, _ in accepted
        ):
            continue  # element już wykryty: VLM nie dubluje OpenCV
        if answer.kind == "char_boxes" and not answer.cells:
            review.append(
                ReviewItem(code=VLM_INVALID_RESPONSE, page=page, detail="kratki bez cells")
            )
            continue
        accepted.append((box, answer))
    limit = settings.max_added_per_page
    if len(accepted) > limit:
        review.append(
            ReviewItem(
                code=VLM_INVALID_RESPONSE,
                page=page,
                detail=f"odrzucono {len(accepted) - limit} dodanych elementów ponad limit {limit}",
            )
        )
        accepted = accepted[:limit]
    accepted.sort(key=lambda item: (item[0].y0, item[0].x0))
    elements: list[VisualElement] = []
    for index, (box, answer) in enumerate(accepted, start=1):
        kind = ElementKind(answer.kind)
        role = ElementRole(answer.role)
        if role is not ElementRole.UNKNOWN and role not in ROLES_FOR_KIND[kind]:
            role = ElementRole.UNKNOWN
        element = VisualElement(
            id=f"vlm-p{page}-{index:03d}",
            page=page,
            kind=kind,
            bbox=box,
            cells=answer.cells if kind is ElementKind.CHAR_BOXES else None,
            label_block_id=answer.label_block_id if answer.label_block_id in block_ids else None,
            role=role,
            source=ElementSource.VLM,
            confidence=ADDED_CONFIDENCE,
            confidence_reasons=(VLM_ONLY_ELEMENT,),
        )
        if not has_area(element.bbox):
            continue
        elements.append(element)
        review.append(ReviewItem(code=VLM_ONLY_ELEMENT, page=page, element_id=element.id))
    return elements, review


def assign_roles(
    page: int,
    image: ImageArray,
    elements: Sequence[VisualElement],
    blocks: Sequence[TextBlock],
    client: VlmClient,
    settings: VlmSettings,
) -> RolesResult:
    """Tryb 1 dla jednej strony; nie zgłasza wyjątków VLM (wynik niesie status i kody)."""
    blocks = [b for b in blocks if b.page == page]
    marks = elements if settings.draw_marks else ()
    picture = page_image(image, settings.image_max_side_px, marks)
    try:
        answer = ask(
            client,
            TASK,
            load_prompt(settings.roles_prompt),
            prompt_values(page, elements, blocks, settings),
            picture,
            RolesResponse,
        )
    except VlmError as error:
        return _failed(page, elements, error)

    by_block = {b.id: b for b in blocks}
    by_id: dict[str, ElementRole | None] = {}
    labels: dict[str, str | None] = {}
    review: list[ReviewItem] = []
    known = {e.id: e for e in elements}
    for item in answer.value.elements:
        if item.id not in known or item.id in by_id:
            review.append(
                ReviewItem(
                    code=VLM_INVALID_RESPONSE,
                    page=page,
                    element_id=item.id,
                    detail="ID spoza listy",
                )
            )
            continue
        role = ElementRole(item.role)
        if role is not ElementRole.UNKNOWN and role not in ROLES_FOR_KIND[known[item.id].kind]:
            review.append(
                ReviewItem(
                    code=VLM_INVALID_RESPONSE,
                    page=page,
                    element_id=item.id,
                    detail=f"rola {role.value} niezgodna z kategorią {known[item.id].kind.value}",
                )
            )
            role = ElementRole.UNKNOWN
        by_id[item.id] = role
        labels[item.id] = item.label_block_id if item.label_block_id in by_block else None

    updated: list[VisualElement] = []
    for element in elements:
        role = by_id.get(element.id) or ElementRole.UNKNOWN
        label = labels.get(element.id)
        changes: dict[str, object] = {"role": role, "label_block_id": label}
        if role is not ElementRole.UNKNOWN:
            changes["source"] = ElementSource.BOTH
        new = element.model_copy(update=changes)
        if role is ElementRole.UNKNOWN:
            new = _with_reason(new, NO_ROLE)
            review.append(ReviewItem(code=NO_ROLE, page=page, element_id=element.id))
        block = by_block.get(label) if label else None
        if (
            block is not None
            and block.confidence is not None
            and block.confidence < settings.low_ocr_confidence
        ):
            new = _with_reason(new, LOW_OCR_CONFIDENCE)
            review.append(
                ReviewItem(
                    code=LOW_OCR_CONFIDENCE, page=page, element_id=element.id, label=block.text
                )
            )
        updated.append(new)

    added, added_review = _added(page, answer.value.added, updated, set(by_block), settings)
    return RolesResult(
        elements=tuple(container_cells_skip(updated + added)),
        review=tuple(review + added_review),
        status="ok",
        attempts=answer.attempts,
        usage=answer.usage,
        cached=answer.cached,
    )
