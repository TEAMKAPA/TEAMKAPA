"""Etap VLM kanału wizualnego (#11): tryb 1 na każdej stronie, tryb 2 według ``ACCURACY_MODE``.

- ``max`` (domyślny): podwójny odczyt każdej strony.
- ``balanced``: podwójny odczyt tylko stron z niską pewnością (``needs_double_read``): tryb 1 bez
  powodzenia, kod strony ``LOW_CONTRAST`` lub ``SKEW_CORRECTED``, > 20% elementów bez roli,
  element dodany przez VLM, etykieta o niskiej pewności OCR, średnia pewność elementów < 0,7
  albo strona bez bloków tekstu (skan bez warstwy tekstowej).

Kontrola kratek: element ``char_boxes`` z etykietą (blok tekstu), z którą liczba kratek jest
sprzeczna (np. PESEL i 10 kratek), dostaje ``CELLS_LABEL_CONFLICT``. Błędy VLM nie przerywają
etapu: wynik niesie kody ``VLM_UNAVAILABLE`` / ``VLM_INVALID_RESPONSE``, a pipeline działa dalej.
"""

import time
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from backend.worker.vision.models import ElementKind, ElementRole, ImageArray, VisualElement
from backend.worker.vision.vlm.client import VlmClient, VlmUsage
from backend.worker.vision.vlm.codes import (
    CELLS_LABEL_CONFLICT,
    LOW_OCR_CONFIDENCE,
    MISSING_IN_VLM,
    NO_ROLE,
    VLM_ONLY_ELEMENT,
    ReviewItem,
)
from backend.worker.vision.vlm.config import AccuracyMode, VlmSettings
from backend.worker.vision.vlm.double_read import (
    Comparison,
    FieldsRead,
    FormField,
    cells_conflict,
    compare,
    expected_cells,
    read_fields,
)
from backend.worker.vision.vlm.roles import RolesResult, TextBlock, assign_roles

LOW_PAGE_REASONS = frozenset({"LOW_CONTRAST", "SKEW_CORRECTED"})
NO_ROLE_SHARE = 0.2
LOW_MEAN_CONFIDENCE = 0.7


@dataclass(frozen=True)
class PageInput:
    page: int
    image: ImageArray
    elements: tuple[VisualElement, ...]  # wynik detektorów OpenCV strony
    reasons: tuple[str, ...] = ()  # kody strony z detekcji (LOW_CONTRAST, SKEW_CORRECTED)


@dataclass(frozen=True)
class PageOutcome:
    page: int
    roles_status: str
    double_read: bool
    double_read_reason: str
    fields_status: str | None = None


@dataclass(frozen=True)
class VlmStageResult:
    mode: AccuracyMode
    elements: tuple[VisualElement, ...]
    review: tuple[ReviewItem, ...]
    pages: tuple[PageOutcome, ...]
    vlm_fields: tuple[FormField, ...]
    comparison: Comparison | None
    usage: VlmUsage
    duration_s: float
    notes: tuple[str, ...] = field(default_factory=tuple)

    def report(self) -> dict[str, Any]:
        """Raport przeglądu: liczności kodów i pozycje (pozycje zawierają etykiety pól)."""
        return {
            "mode": self.mode.value,
            "pages": [vars(p) for p in self.pages],
            "codes": dict(sorted(Counter(r.code for r in self.review).items())),
            "items": [r.model_dump(exclude_defaults=True) for r in self.review],
            "matched_fields": len(self.comparison.matches) if self.comparison else None,
            "usage": {
                "prompt_tokens": self.usage.prompt_tokens,
                "completion_tokens": self.usage.completion_tokens,
            },
            "duration_s": round(self.duration_s, 3),
            "notes": list(self.notes),
        }


def needs_double_read(
    page: PageInput, roles: RolesResult, blocks: Sequence[TextBlock], settings: VlmSettings
) -> tuple[bool, str]:
    """Czy stronę czytać trybem 2; zwraca też powód (do raportu)."""
    if settings.accuracy_mode is AccuracyMode.MAX:
        return True, "max"
    if roles.status != "ok":
        return True, f"tryb 1: {roles.status}"
    if LOW_PAGE_REASONS & set(page.reasons):
        return True, "kod strony: " + ",".join(sorted(LOW_PAGE_REASONS & set(page.reasons)))
    if not any(b.page == page.page for b in blocks):
        return True, "brak bloków tekstu"
    cv = [e for e in roles.elements if e.id.startswith("cv-")]
    if cv and sum(e.role is ElementRole.UNKNOWN for e in cv) / len(cv) > NO_ROLE_SHARE:
        return True, "elementy bez roli"
    codes = {r.code for r in roles.review}
    if VLM_ONLY_ELEMENT in codes or LOW_OCR_CONFIDENCE in codes:
        return True, "elementy do przeglądu"
    if cv and sum(e.confidence for e in cv) / len(cv) < LOW_MEAN_CONFIDENCE:
        return True, "niska średnia pewność"
    return False, "pewna strona"


def _cells_review(
    elements: Sequence[VisualElement], blocks: Sequence[TextBlock]
) -> tuple[list[VisualElement], list[ReviewItem]]:
    by_block = {b.id: b for b in blocks}
    updated: list[VisualElement] = []
    review: list[ReviewItem] = []
    for element in elements:
        block = by_block.get(element.label_block_id or "")
        if (
            element.kind is ElementKind.CHAR_BOXES
            and block
            and cells_conflict(block.text, element.cells)
        ):
            expected = sorted(expected_cells(block.text) or ())
            review.append(
                ReviewItem(
                    code=CELLS_LABEL_CONFLICT,
                    page=element.page,
                    element_id=element.id,
                    label=block.text,
                    detail=f"kratek {element.cells}, oczekiwano {expected}",
                )
            )
            if CELLS_LABEL_CONFLICT not in element.confidence_reasons:
                reasons = (*element.confidence_reasons, CELLS_LABEL_CONFLICT)
                element = element.model_copy(update={"confidence_reasons": reasons})
        updated.append(element)
    return updated, review


def run_vlm_stage(
    pages: Sequence[PageInput],
    blocks: Sequence[TextBlock],
    text_fields: Sequence[FormField] | None,
    client: VlmClient,
    settings: VlmSettings,
) -> VlmStageResult:
    """Tryb 1 i 2 dla dokumentu; ``text_fields``: pola z kanału tekstowego albo None."""
    started = time.monotonic()
    elements: list[VisualElement] = []
    review: list[ReviewItem] = []
    outcomes: list[PageOutcome] = []
    reads: list[FieldsRead] = []
    prompt_tokens = completion_tokens = 0
    for page in pages:
        roles = assign_roles(page.page, page.image, page.elements, blocks, client, settings)
        prompt_tokens += roles.usage.prompt_tokens
        completion_tokens += roles.usage.completion_tokens
        checked, cells_items = _cells_review(roles.elements, blocks)
        elements.extend(checked)
        review.extend(roles.review)
        review.extend(cells_items)
        wanted, reason = needs_double_read(page, roles, blocks, settings)
        fields_status = None
        if wanted:
            read = read_fields(page.page, page.image, client, settings)
            reads.append(read)
            review.extend(read.review)
            fields_status = read.status
            prompt_tokens += read.usage.prompt_tokens
            completion_tokens += read.usage.completion_tokens
        outcomes.append(PageOutcome(page.page, roles.status, wanted, reason, fields_status))

    vlm_fields = tuple(f for read in reads for f in read.fields)
    comparison = None
    notes: list[str] = []
    if text_fields is not None and reads:
        read_pages = {r.page for r in reads if r.status == "ok"}
        all_read = read_pages == {p.page for p in pages}
        scoped = [f for f in text_fields if f.page is None or f.page in read_pages]
        comparison = compare(scoped, list(vlm_fields))
        items = list(comparison.review)
        if not all_read and any(f.page is None for f in scoped):
            # Pola z tekstu bez numeru strony (np. JSON referencyjny) przy części stron
            # przeczytanych: brak pary nie znaczy, że VLM pola nie widzi.
            items = [r for r in items if r.code != MISSING_IN_VLM]
            notes.append("MISSING_IN_VLM pominięte: nie wszystkie strony miały podwójny odczyt")
        review.extend(items)
    elif text_fields is None:
        notes.append("brak pól z kanału tekstowego: podwójny odczyt bez porównania")

    no_role = sum(r.code == NO_ROLE for r in review)
    if no_role:
        notes.append(f"elementy bez roli: {no_role}")
    return VlmStageResult(
        mode=settings.accuracy_mode,
        elements=tuple(elements),
        review=tuple(review),
        pages=tuple(outcomes),
        vlm_fields=vlm_fields,
        comparison=comparison,
        usage=VlmUsage(prompt_tokens, completion_tokens),
        duration_s=time.monotonic() - started,
        notes=tuple(notes),
    )
