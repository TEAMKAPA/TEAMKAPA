"""Linie poziome i pionowe (C.3).

- Odcinki: otwarcie morfologiczne maski tuszu jądrem o długości ``line_min_mm`` i szerokości 1 px
  (poziomo i pionowo). Tekst i kropki znikają, zostają odcinki co najmniej tej długości.
- Struktura: tusz z liniami domkniętymi na przerwach do ``line_bridge_mm`` (skany, kreski
  przerywane); na niej C.4 szuka zamkniętych prostokątów.
- ``text_line``: poziomy odcinek nie grubszy niż ``text_line_max_thickness_mm`` (grubsze paski to
  separatory sekcji) i nie krótszy niż ``text_line_min_mm``, który nie jest krawędzią prostokąta.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

import cv2
import numpy as np

from backend.worker.vision.config import DetectConfig
from backend.worker.vision.detect.preprocess import Mask, PixelBox
from backend.worker.vision.detect.scale import Scale


@dataclass(frozen=True)
class Segment:
    box: PixelBox  # krawędzie pikseli

    @property
    def length(self) -> int:
        return max(self.box[2] - self.box[0], self.box[3] - self.box[1])

    @property
    def thickness(self) -> int:
        return min(self.box[2] - self.box[0], self.box[3] - self.box[1])


@dataclass(frozen=True)
class LineMaps:
    horizontal: Mask
    vertical: Mask
    structure: Mask  # tusz ∪ linie z domkniętymi przerwami
    h_segments: tuple[Segment, ...]
    v_segments: tuple[Segment, ...]


def _open(ink: Mask, size: tuple[int, int]) -> Mask:
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, size)
    opened = cv2.morphologyEx(ink.astype(np.uint8), cv2.MORPH_OPEN, kernel)
    return np.asarray(opened, dtype=np.uint8) > 0


def _close(mask: Mask, size: tuple[int, int]) -> Mask:
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, size)
    closed = cv2.morphologyEx(mask.astype(np.uint8), cv2.MORPH_CLOSE, kernel)
    return np.asarray(closed, dtype=np.uint8) > 0


def components(mask: Mask) -> list[PixelBox]:
    """Ramki składowych spójnych (8-sąsiedztwo), w kolejności etykiet OpenCV (deterministycznej)."""
    count, _, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)
    boxes = []
    for label in range(1, count):
        x, y, w, h = (int(v) for v in stats[label, :4])
        boxes.append((x, y, x + w, y + h))
    return boxes


def find_lines(ink: Mask, cfg: DetectConfig, scale: Scale) -> LineMaps:
    length = scale.ipx(cfg.line_min_mm, minimum=3)
    bridge = scale.ipx(cfg.line_bridge_mm, minimum=1) + 1
    horizontal = _open(ink, (length, 1))
    vertical = _open(ink, (1, length))
    structure = ink | _close(horizontal, (bridge, 1)) | _close(vertical, (1, bridge))
    return LineMaps(
        horizontal=horizontal,
        vertical=vertical,
        structure=structure,
        h_segments=tuple(Segment(b) for b in components(horizontal)),
        v_segments=tuple(Segment(b) for b in components(vertical)),
    )


def _on_border(segment: Segment, rects: Sequence[PixelBox], band: int) -> bool:
    """Czy poziomy odcinek leży w ≥ 50% długości na górnych lub dolnych krawędziach prostokątów."""
    x0, y0, x1, y1 = segment.box
    spans = []
    for rect in rects:
        top = y0 >= rect[1] - band and y1 <= rect[1] + band
        bottom = y0 >= rect[3] - band and y1 <= rect[3] + band
        if top or bottom:
            start, stop = max(x0, rect[0]), min(x1, rect[2])
            if stop > start:
                spans.append((start, stop))
    covered, end = 0, x0
    for start, stop in sorted(spans):
        start = max(start, end)
        if stop > start:
            covered += stop - start
            end = stop
    return covered >= 0.5 * (x1 - x0)


def text_line_candidates(
    lines: LineMaps, rects: Iterable[PixelBox], cfg: DetectConfig, scale: Scale
) -> list[PixelBox]:
    """Poziome linie do wpisywania: cienkie, długie, niebędące krawędzią prostokąta."""
    min_length = scale.px(cfg.text_line_min_mm)
    max_thickness = max(1.0, scale.px(cfg.text_line_max_thickness_mm))
    band = scale.ipx(cfg.border_max_mm)
    boxes: Sequence[PixelBox] = list(rects)
    result = []
    for segment in lines.h_segments:
        if segment.length < min_length or segment.thickness > max_thickness:
            continue
        if _on_border(segment, boxes, band):
            continue
        result.append(segment.box)
    return result
