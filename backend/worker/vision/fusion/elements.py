"""Fuzja elementów strony: OpenCV × VLM tryb 1 (krok 4, brief 7.6).

Reguły (sytuacja → decyzja, poziom, kod):

- ``cv-*`` z rolą VLM zgodną z kategorią → ``accepted``, HIGH,
- ``cv-*`` bez roli albo VLM zawiódł na stronie → ``review``, najwyżej MEDIUM
  (``NO_ROLE``, ``VLM_UNAVAILABLE``, ``VLM_INVALID_RESPONSE``),
- tylko ``vlm-*`` ze śladem w pikselach albo w pustym obszarze → ``added``, LOW
  (``VLM_ONLY_ELEMENT``),
- ``vlm-*`` sprzeczny z obrazem (wiersz tekstu) → usunięty, w przeglądzie ``VLM_ONLY_REJECTED``,
- kratki zgodne z etykietą (PESEL 11, NIP 10, …) → typ potwierdzony (``CELLS_LABEL_MATCH``),
- kratki sprzeczne z etykietą → typ z etykiety, LOW (``CELLS_LABEL_CONFLICT``),
- grupa wyboru z nieznanym ``multi`` → najwyżej MEDIUM (``GROUP_MULTI_UNKNOWN``),
- strona z ``DPI_REDUCED``, ``TALL_IMAGE_UNSPLIT`` albo ``DPI_UNKNOWN`` bez estymaty → najwyżej
  MEDIUM dla wszystkich elementów strony (kod strony).

Poziom wynika z kodów (``codes.level_for``), a ``confidence`` to liczba w paśmie poziomu z
położeniem równym pewności detektora (``models.banded``). Etykieta kratek pochodzi z bloku tekstu
(źródła), nie z VLM. Ten sam wynik VLM i ta sama strona dają zawsze ten sam wynik fuzji.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal

from backend.shared.schemas.quality import ConfidenceLevel, Severity
from backend.worker.vision.errors import IssueCode
from backend.worker.vision.fusion import codes
from backend.worker.vision.fusion.config import FusionConfig
from backend.worker.vision.fusion.models import (
    Decision,
    FusedElement,
    RejectedElement,
    banded,
)
from backend.worker.vision.models import ElementKind, ElementRole, PageMeta, VisualElement
from backend.worker.vision.vlm import codes as vlm
from backend.worker.vision.vlm.codes import ReviewItem
from backend.worker.vision.vlm.double_read import expected_cells
from backend.worker.vision.vlm.roles import TextBlock

VlmPageStatus = Literal["ok", "invalid", "unavailable", "not_run"]

PAGE_CAP_CODES = (IssueCode.DPI_REDUCED, IssueCode.TALL_IMAGE_UNSPLIT)

# Położenie w paśmie LOW elementu dodanego przez VLM: im mocniejszy ślad, tym wyżej.
ADDED_POSITION: dict[str | None, float] = {
    codes.WEAK_VISUAL_EVIDENCE: 0.8,
    codes.NO_VISUAL_EVIDENCE: 0.4,
    None: 0.6,  # bez kontroli pikseli (brak obrazu strony)
}


@dataclass(frozen=True)
class Verdict:
    """Wynik kontroli pikseli elementu ``vlm-*`` (``evidence.make_verifier``)."""

    keep: bool
    code: str | None = None  # WEAK_VISUAL_EVIDENCE, NO_VISUAL_EVIDENCE, VLM_ONLY_REJECTED
    reason: str = ""  # np. DOTS, LINE, STROKES, BLANK, TEXT_ROW
    detail: str = ""


Verifier = Callable[[VisualElement], Verdict]


@dataclass(frozen=True)
class PageFusionInput:
    page: int
    elements: tuple[VisualElement, ...]  # wynik etapu VLM strony albo sam wynik OpenCV
    vlm_status: VlmPageStatus
    page_codes: tuple[str, ...] = ()  # z ``page_codes_from_meta``
    verify: Verifier | None = None  # kontrola pikseli vlm-* (None: bez obrazu)


@dataclass(frozen=True)
class PageFusion:
    page: int
    elements: tuple[FusedElement, ...]
    rejected: tuple[RejectedElement, ...]
    review: tuple[ReviewItem, ...]  # pozycje przeglądu dodane przez fuzję


def page_codes_from_meta(meta: PageMeta) -> tuple[str, ...]:
    """Kody strony z Etapu A obniżające pewność elementów (decyzja z Etapu A)."""
    result: list[str] = []
    for issue in meta.issues:
        if issue.code in PAGE_CAP_CODES or (
            issue.code is IssueCode.DPI_UNKNOWN and issue.severity is Severity.WARNING
        ):
            result.append(issue.code.value)
    return tuple(dict.fromkeys(result))


def _vlm_codes(element: VisualElement, status: VlmPageStatus) -> list[str]:
    if status == "ok":
        return [] if element.role is not ElementRole.UNKNOWN else [vlm.NO_ROLE]
    if status == "invalid":
        return [vlm.VLM_INVALID_RESPONSE]
    return [vlm.VLM_UNAVAILABLE]  # VLM niedostępny albo nieuruchomiony


def _cells_codes(element: VisualElement, blocks: dict[str, TextBlock]) -> list[str]:
    if element.kind is not ElementKind.CHAR_BOXES or element.label_block_id is None:
        return []
    block = blocks.get(element.label_block_id)
    expected = expected_cells(block.text) if block else None
    if expected is None or element.cells is None:
        return []
    return [codes.CELLS_LABEL_MATCH if element.cells in expected else vlm.CELLS_LABEL_CONFLICT]


def _fused(
    element: VisualElement,
    reasons: Sequence[str],
    decision: Decision | None,
    position: float,
    cfg: FusionConfig,
) -> FusedElement:
    unique = tuple(dict.fromkeys(reasons))
    level = codes.level_for(unique)
    if decision is None:
        decision = Decision.ACCEPTED if level is ConfidenceLevel.HIGH else Decision.REVIEW
    return FusedElement(
        **element.model_dump(exclude={"confidence", "confidence_reasons"}),
        confidence=banded(level, position, high=cfg.high, low=cfg.low),
        confidence_reasons=unique,
        decision=decision,
        level=level,
    )


def fuse_page(
    data: PageFusionInput, blocks: Sequence[TextBlock], cfg: FusionConfig | None = None
) -> PageFusion:
    """Fuzja elementów jednej strony; elementy zachowują kolejność i ID z kroku 3."""
    cfg = cfg or FusionConfig()
    by_block = {b.id: b for b in blocks if b.page == data.page}
    fused: list[FusedElement] = []
    rejected: list[RejectedElement] = []
    review: list[ReviewItem] = []
    for element in data.elements:
        if element.page != data.page:
            raise ValueError(f"element {element.id} nie należy do strony {data.page}")
        cells = _cells_codes(element, by_block)
        if vlm.CELLS_LABEL_CONFLICT in cells and (
            vlm.CELLS_LABEL_CONFLICT not in element.confidence_reasons
        ):  # etap VLM nie zgłosił konfliktu (np. etykieta spoza jego bloków): zgłasza fuzja
            review.append(
                ReviewItem(
                    code=vlm.CELLS_LABEL_CONFLICT,
                    page=element.page,
                    element_id=element.id,
                    detail=f"kratek {element.cells}",
                )
            )
        group = (
            [codes.GROUP_MULTI_UNKNOWN]
            if element.kind is ElementKind.CHECKBOX_GROUP
            and element.role is ElementRole.CHOICE
            and element.multi is None
            else []
        )
        base = [*element.confidence_reasons, *cells, *group, *data.page_codes]

        if element.id.startswith("vlm-"):
            verdict = data.verify(element) if data.verify else Verdict(keep=True)
            if not verdict.keep:
                rejected.append(
                    RejectedElement(
                        element=element,
                        code=codes.VLM_ONLY_REJECTED,
                        reason=verdict.reason,
                        detail=verdict.detail,
                    )
                )
                review.append(
                    ReviewItem(
                        code=codes.VLM_ONLY_REJECTED,
                        page=element.page,
                        element_id=element.id,
                        detail=f"{verdict.reason}: {verdict.detail}".rstrip(": "),
                    )
                )
                continue
            reasons = [vlm.VLM_ONLY_ELEMENT, *base]
            if verdict.code:
                reasons.append(verdict.code)
            fused.append(
                _fused(element, reasons, Decision.ADDED, ADDED_POSITION.get(verdict.code, 0.6), cfg)
            )
            continue

        reasons = [*base, *_vlm_codes(element, data.vlm_status)]
        fused.append(_fused(element, reasons, None, element.confidence, cfg))
    return PageFusion(data.page, tuple(fused), tuple(rejected), tuple(review))


def fuse_document(
    pages: Sequence[PageFusionInput], blocks: Sequence[TextBlock], cfg: FusionConfig | None = None
) -> tuple[PageFusion, ...]:
    return tuple(fuse_page(p, blocks, cfg) for p in sorted(pages, key=lambda p: p.page))
