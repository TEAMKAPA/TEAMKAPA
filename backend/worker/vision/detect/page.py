"""Detekcja elementów jednej strony: złożenie detektorów, ID i pewność (C.7).

Kolejność: preprocessing → linie → prostokąty (kratki, checkboxy, komórki) → ramki sekcji →
linie kropkowane → linie do wpisywania (bez krawędzi prostokątów) → kandydaci na grupy.

**Stabilne ID** ``cv-p{strona}-{nnn}`` (numeracja od 001): sortowanie elementów w układzie strony
po (wiersz, x0, y1, x1, kategoria), gdzie wiersz = ``floor(y0 / id_row_mm)``. Zaokrąglenie y do
wiersza sprawia, że elementy jednego wiersza są numerowane od lewej mimo różnicy 1–2 px w y0.
Ten sam obraz i ta sama konfiguracja dają zawsze te same elementy, kolejność i ID.

**Pewność:** wartość detektora (np. 0,9 komórka, 0,85 checkbox, 0,8 kropki, 0,7 linia, 0,3 grupa)
mnożona przez 0,8 przy ``LOW_CONTRAST`` i 0,95 przy ``SKEW_CORRECTED``; kody powodów elementu
i strony trafiają do ``confidence_reasons``. Rola zawsze ``unknown`` (rozstrzyga VLM i fuzja).
"""

from dataclasses import dataclass

from backend.shared.schemas.quality import BoundingBox
from backend.worker.vision.config import DetectConfig
from backend.worker.vision.detect.candidates import Candidate
from backend.worker.vision.detect.dots import dotted_lines
from backend.worker.vision.detect.groups import checkbox_groups
from backend.worker.vision.detect.lines import find_lines, text_line_candidates
from backend.worker.vision.detect.preprocess import (
    LOW_CONTRAST,
    SKEW_CORRECTED,
    PixelBox,
    Prepared,
    prepare,
)
from backend.worker.vision.detect.rects import classify_rects, find_rects, find_sections
from backend.worker.vision.detect.scale import Scale
from backend.worker.vision.models import (
    ElementKind,
    ElementRole,
    ElementSource,
    ImageArray,
    VisualElement,
)

TEXT_LINE_CONFIDENCE = 0.7
KIND_ORDER = {kind: index for index, kind in enumerate(ElementKind)}
PAGE_FACTORS = {LOW_CONTRAST: 0.8, SKEW_CORRECTED: 0.95}


@dataclass(frozen=True)
class PageDetection:
    elements: tuple[VisualElement, ...]
    angle: float  # wykryte pochylenie (stopnie)
    dpi: float  # efektywne DPI użyte do progów
    reasons: tuple[str, ...]  # kody strony (LOW_CONTRAST, SKEW_CORRECTED)


def find_candidates(prepared: Prepared, cfg: DetectConfig, scale: Scale) -> list[Candidate]:
    """Wszyscy kandydaci strony w układzie obrazu wyprostowanego."""
    lines = find_lines(prepared.ink, cfg, scale)
    rects = find_rects(lines.structure, cfg, scale)
    shapes = classify_rects(rects, cfg, scale)
    sections = find_sections(lines.structure, shapes, cfg, scale)
    dots = dotted_lines(prepared.light_ink, cfg, scale)
    borders = [r.box for r in rects] + [c.box for c in shapes + sections]
    text_lines = [
        Candidate(kind=ElementKind.TEXT_LINE, box=box, confidence=TEXT_LINE_CONFIDENCE)
        for box in text_line_candidates(lines, borders, cfg, scale)
    ]
    groups = checkbox_groups(shapes, prepared.ink, lines, cfg, scale, [d.box for d in dots])
    return shapes + sections + dots + text_lines + groups


def _sort_key(box: PixelBox, kind: ElementKind, row: int) -> tuple[int, int, int, int, int]:
    return box[1] // row, box[0], box[3], box[2], KIND_ORDER[kind]


def detect_image(
    image: ImageArray, page: int, dpi: float, cfg: DetectConfig | None = None
) -> PageDetection:
    """Elementy strony ``page`` (numer od 1) obrazu RGB o efektywnym DPI ``dpi``."""
    cfg = cfg or DetectConfig()
    scale = Scale(dpi)
    prepared = prepare(image, cfg, scale)
    candidates = find_candidates(prepared, cfg, scale)
    width, height = prepared.width, prepared.height
    row = scale.ipx(cfg.id_row_mm)

    placed = []
    seen: set[tuple[ElementKind, PixelBox]] = set()
    for candidate in candidates:
        box = prepared.to_page(candidate.box)
        key = (candidate.kind, candidate.box)
        if box[2] > box[0] and box[3] > box[1] and key not in seen:
            seen.add(key)
            placed.append((box, candidate))
    placed.sort(key=lambda item: (*_sort_key(item[0], item[1].kind, row), item[1].box))
    ids = {
        (candidate.kind, candidate.box): f"cv-p{page}-{index:03d}"
        for index, (_, candidate) in enumerate(placed, start=1)
    }
    factor = 1.0
    for reason in prepared.reasons:
        factor *= PAGE_FACTORS.get(reason, 1.0)

    elements = []
    for box, candidate in placed:
        options = tuple(
            ids[(ElementKind.CHECKBOX, member)]
            for member in candidate.members
            if (ElementKind.CHECKBOX, member) in ids
        )
        is_group = candidate.kind is ElementKind.CHECKBOX_GROUP
        elements.append(
            VisualElement(
                id=ids[(candidate.kind, candidate.box)],
                page=page,
                kind=candidate.kind,
                bbox=BoundingBox(
                    x0=box[0] / width, y0=box[1] / height, x1=box[2] / width, y1=box[3] / height
                ),
                cells=candidate.cells,
                role=ElementRole.UNKNOWN,
                options=options if is_group else (),
                multi=None,
                source=ElementSource.OPENCV,
                confidence=round(candidate.confidence * factor, 3),
                confidence_reasons=candidate.reasons + prepared.reasons,
            )
        )
    return PageDetection(
        elements=tuple(elements), angle=prepared.angle, dpi=dpi, reasons=prepared.reasons
    )
