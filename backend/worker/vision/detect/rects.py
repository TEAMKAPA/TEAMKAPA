"""Prostokąty: checkboxy, kratki znakowe, komórki tabeli, ramki sekcji (C.4).

1. **Wnętrza:** składowe spójne tła (4-sąsiedztwo) na masce struktury (``lines.find_lines``)
   pogrubionej o ``gap_close_mm`` (domyka przerwy na złączach linii 1 px; ramka jest potem zwężana
   o tyle samo), które
   nie dotykają krawędzi strony i mają boki ≥ ``rect_min_mm``. Po wypełnieniu dziur (tekst,
   checkbox w komórce) wypełniają swoją ramkę w ≥ ``rect_fill`` (pętle liter są zaokrąglone), a bez
   wypełnienia w ≥ ``rect_open`` (pierścień tła między ramką sekcji a tabelą to nie komórka).
2. **Obramowanie:** każdy bok wnętrza przesuwany na zewnątrz, dopóki kolejna kolumna (wiersz) jest
   tuszem na ≥ ``border_coverage`` długości, najwyżej o ``border_max_mm``. Bok bez obramowania
   odrzuca prostokąt. Ramka obejmuje więc linie, jak w etykietach (``LABELING.md``, zasada 4).
3. **Klasyfikacja** po wymiarach zewnętrznych w mm:
   - kratki: ≥ 2 sąsiadujące komórki (wspólna krawędź, przerwa ≤ ``char_gap_mm``) w jednym wierszu,
     tej samej wysokości (± ``char_height_tolerance``), nie większe niż ``char_cell_max_mm``;
     ``cells`` = liczba komórek,
   - checkbox: pojedynczy kwadrat (proporcja ≤ ``square_aspect``) o boku
     ``checkbox_min_mm``–``checkbox_max_mm``,
   - komórka tabeli: prostokąt co najmniej ``cell_min_w_mm`` × ``cell_min_h_mm``.
4. **Ramka sekcji:** składowa spójna tuszu, której ramka ma tusz na wszystkich czterech bokach,
   zawiera ≥ ``section_min_children`` wykrytych elementów i nie pokrywa się z żadnym z nich (np.
   z grupą kratek). Ramka wewnątrz innej, obejmująca te same elementy (tabela w ramce sekcji,
   podwójna linia), jest pomijana: zostaje zewnętrzna.
"""

from collections.abc import Sequence
from dataclasses import dataclass

import cv2
import numpy as np

from backend.worker.vision.config import DetectConfig
from backend.worker.vision.detect.candidates import Candidate
from backend.worker.vision.detect.preprocess import Mask, PixelBox
from backend.worker.vision.detect.scale import Scale
from backend.worker.vision.models import ElementKind

CELLS_IRREGULAR = "CELLS_IRREGULAR"
PAGE_FRAME_FRACTION = 0.9  # ramka obejmująca prawie całą stronę to obramowanie strony


@dataclass(frozen=True)
class Rect:
    box: PixelBox  # zewnętrzna krawędź obramowania
    interior: PixelBox

    @property
    def width(self) -> int:
        return self.box[2] - self.box[0]

    @property
    def height(self) -> int:
        return self.box[3] - self.box[1]


def _area(box: PixelBox) -> int:
    return max(box[2] - box[0], 0) * max(box[3] - box[1], 0)


def iou(a: PixelBox, b: PixelBox) -> float:
    inter = _area((max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])))
    union = _area(a) + _area(b) - inter
    return inter / union if union else 0.0


def _fractions(labels: np.ndarray, label: int, box: PixelBox) -> tuple[float, float]:
    """Część ramki zajęta przez obszar tła: bez wypełnienia dziur i z wypełnieniem."""
    x0, y0, x1, y1 = box
    region = (labels[y0:y1, x0:x1] == label).astype(np.uint8)
    contours, _ = cv2.findContours(region, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    filled = np.zeros_like(region)
    cv2.drawContours(filled, contours, -1, 1, thickness=cv2.FILLED)
    return float(region.mean()), float(filled.mean())


def _expand(
    structure: Mask, interior: PixelBox, cfg: DetectConfig, scale: Scale, grow_px: int = 0
) -> PixelBox | None:
    """Rozszerza wnętrze do zewnętrznej krawędzi obramowania; ``None``, gdy bok nie ma linii."""
    height, width = structure.shape
    limit = scale.ipx(cfg.border_max_mm) + 2 * grow_px
    x0, y0, x1, y1 = interior

    def coverage_col(x: int) -> float:
        return float(structure[y0:y1, x].mean()) if 0 <= x < width else 0.0

    def coverage_row(y: int) -> float:
        return float(structure[y, x0:x1].mean()) if 0 <= y < height else 0.0

    def grow(start: int, step: int, coverage: object) -> int:
        count = 0
        position = start
        while count < limit and coverage(position) >= cfg.border_coverage:  # type: ignore[operator]
            count += 1
            position += step
        return count

    left = grow(x0 - 1, -1, coverage_col)
    right = grow(x1, 1, coverage_col)
    top = grow(y0 - 1, -1, coverage_row)
    bottom = grow(y1, 1, coverage_row)
    if min(left, right, top, bottom) == 0:
        return None
    return x0 - left, y0 - top, x1 + right, y1 + bottom


def find_rects(structure: Mask, cfg: DetectConfig, scale: Scale) -> list[Rect]:
    """Zamknięte prostokąty strony (kolejność: góra, potem lewo)."""
    # Struktura pogrubiona o r px domyka przerwy na złączach cienkich linii (także w zewnętrznych
    # narożnikach, których nie domyka zamknięcie morfologiczne); ramka jest potem zwężana o r px.
    r = scale.ipx(cfg.gap_close_mm, minimum=1)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2 * r + 1, 2 * r + 1))
    grown: Mask = np.asarray(cv2.dilate(structure.astype(np.uint8), kernel), dtype=np.uint8) > 0
    background = (~grown).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(background, connectivity=4)
    height, width = structure.shape
    min_side = scale.px(cfg.rect_min_mm) - 2 * r
    rects = []
    for label in range(1, count):
        x, y, w, h = (int(v) for v in stats[label, :4])
        if x == 0 or y == 0 or x + w == width or y + h == height:
            continue  # tło strony
        if w < min_side or h < min_side:
            continue
        if w >= PAGE_FRAME_FRACTION * width and h >= PAGE_FRAME_FRACTION * height:
            continue  # wnętrze obramowania całej strony
        interior = (x, y, x + w, y + h)
        open_part, filled_part = _fractions(labels, label, interior)
        if filled_part < cfg.rect_fill or open_part < cfg.rect_open:
            continue
        grown_box = _expand(grown, interior, cfg, scale, r)
        if grown_box is not None:
            box = (grown_box[0] + r, grown_box[1] + r, grown_box[2] - r, grown_box[3] - r)
            inner = (x - r, y - r, x + w + r, y + h + r)
            rects.append(Rect(box=box, interior=inner))
    rects.sort(key=lambda r: (r.box[1], r.box[0], r.box[3], r.box[2]))
    return rects


def _is_char_cell(rect: Rect, cfg: DetectConfig, scale: Scale) -> bool:
    w, h = scale.mm(rect.width), scale.mm(rect.height)
    longest, shortest = max(w, h), min(w, h)
    return (
        cfg.checkbox_min_mm <= shortest
        and longest <= cfg.char_cell_max_mm
        and longest / shortest <= cfg.char_cell_aspect
    )


def _is_checkbox(rect: Rect, cfg: DetectConfig, scale: Scale) -> bool:
    w, h = scale.mm(rect.width), scale.mm(rect.height)
    longest, shortest = max(w, h), min(w, h)
    return (
        cfg.checkbox_min_mm <= shortest
        and longest <= cfg.checkbox_max_mm
        and longest / shortest <= cfg.square_aspect
    )


def _adjacent(a: Rect, b: Rect, cfg: DetectConfig, scale: Scale) -> bool:
    """``b`` leży bezpośrednio na prawo od ``a`` w tym samym wierszu kratek."""
    tolerance = cfg.char_height_tolerance * max(a.height, b.height)
    same_row = abs(a.box[1] - b.box[1]) <= tolerance and abs(a.height - b.height) <= tolerance
    gap = b.box[0] - a.box[2]
    return same_row and -scale.px(cfg.border_max_mm) <= gap <= scale.px(cfg.char_gap_mm)


def _char_groups(cells: list[Rect], cfg: DetectConfig, scale: Scale) -> list[list[Rect]]:
    """Łańcuchy kratek od lewej do prawej (każda kratka w jednym łańcuchu)."""
    ordered = sorted(cells, key=lambda r: (r.box[0], r.box[1]))
    used: set[int] = set()
    groups = []
    for start, rect in enumerate(ordered):
        if start in used:
            continue
        chain, used_now = [rect], {start}
        current = rect
        while True:
            following = [
                (i, other)
                for i, other in enumerate(ordered)
                if i not in used and i not in used_now and _adjacent(current, other, cfg, scale)
            ]
            if not following:
                break
            index, current = min(following, key=lambda item: (item[1].box[0], item[1].box[1]))
            chain.append(current)
            used_now.add(index)
        used |= used_now
        groups.append(chain)
    return groups


def _union(boxes: Sequence[PixelBox]) -> PixelBox:
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )


def classify_rects(rects: list[Rect], cfg: DetectConfig, scale: Scale) -> list[Candidate]:
    """Kratki, checkboxy i komórki tabeli z prostokątów (bez ramek sekcji)."""
    char_cells = [r for r in rects if _is_char_cell(r, cfg, scale)]
    result: list[Candidate] = []
    grouped: set[PixelBox] = set()
    for chain in _char_groups(char_cells, cfg, scale):
        if len(chain) < 2:
            continue
        widths = [r.width for r in chain]
        regular = max(widths) <= 1.25 * min(widths)
        result.append(
            Candidate(
                kind=ElementKind.CHAR_BOXES,
                box=_union([r.box for r in chain]),
                confidence=0.9 if regular else 0.6,
                reasons=() if regular else (CELLS_IRREGULAR,),
                cells=len(chain),
            )
        )
        grouped.update(r.box for r in chain)
    min_w, min_h = scale.px(cfg.cell_min_w_mm), scale.px(cfg.cell_min_h_mm)
    for rect in rects:
        if rect.box in grouped:
            continue
        if _is_checkbox(rect, cfg, scale):
            result.append(Candidate(kind=ElementKind.CHECKBOX, box=rect.box, confidence=0.85))
        elif max(rect.width, rect.height) >= min_w and min(rect.width, rect.height) >= min_h:
            result.append(Candidate(kind=ElementKind.TABLE_CELL, box=rect.box, confidence=0.9))
    return result


def _inside(inner: PixelBox, outer: PixelBox) -> bool:
    return (
        inner[0] >= outer[0]
        and inner[1] >= outer[1]
        and inner[2] <= outer[2]
        and inner[3] <= outer[3]
    )


def _framed(structure: Mask, box: PixelBox, cfg: DetectConfig, scale: Scale) -> bool:
    """Tusz na każdym boku ramki: w pasie ``frame_band_mm`` przy boku, na ≥ ``border_coverage``.

    Pas (a nie jeden wiersz) toleruje resztkowe pochylenie po prostowaniu i niższe DPI.
    """
    x0, y0, x1, y1 = box
    band = scale.ipx(cfg.frame_band_mm)
    sides = (
        structure[y0 : y0 + band, x0:x1].any(axis=0),
        structure[max(y1 - band, y0) : y1, x0:x1].any(axis=0),
        structure[y0:y1, x0 : x0 + band].any(axis=1),
        structure[y0:y1, max(x1 - band, x0) : x1].any(axis=1),
    )
    return all(float(side.mean()) >= cfg.border_coverage for side in sides)


def find_sections(
    structure: Mask, elements: Sequence[Candidate], cfg: DetectConfig, scale: Scale
) -> list[Candidate]:
    """Ramki sekcji: obrysy składowych tuszu zawierające co najmniej kilka elementów."""
    count, _, stats, _ = cv2.connectedComponentsWithStats(
        structure.astype(np.uint8), connectivity=8
    )
    height, width = structure.shape
    min_w, min_h = scale.px(cfg.cell_min_w_mm), scale.px(cfg.cell_min_h_mm)
    frames: list[tuple[PixelBox, frozenset[PixelBox]]] = []
    for label in range(1, count):
        x, y, w, h = (int(v) for v in stats[label, :4])
        box = (x, y, x + w, y + h)
        if w < min_w or h < min_h:
            continue
        if w >= PAGE_FRAME_FRACTION * width and h >= PAGE_FRAME_FRACTION * height:
            continue
        children = frozenset(e.box for e in elements if _inside(e.box, box))
        if len(children) < cfg.section_min_children:
            continue
        if any(iou(box, e.box) >= 0.9 for e in elements):
            continue
        if _framed(structure, box, cfg, scale):
            frames.append((box, children))
    frames.sort(key=lambda item: (-_area(item[0]), item[0]))
    kept: list[tuple[PixelBox, frozenset[PixelBox]]] = []
    for frame, children in frames:
        # Ramka wewnątrz innej, z tymi samymi elementami (tabela w ramce sekcji, podwójna linia):
        # jedna ramka, zewnętrzna, jak w konwencji oznaczania.
        if any(
            _inside(frame, outer) and children == outer_children for outer, outer_children in kept
        ):
            continue
        if all(iou(frame, other) < 0.9 for other, _ in kept):
            kept.append((frame, children))
    boxes = sorted((frame for frame, _ in kept), key=lambda b: (b[1], b[0]))
    return [Candidate(kind=ElementKind.SECTION_FRAME, box=b, confidence=0.8) for b in boxes]
