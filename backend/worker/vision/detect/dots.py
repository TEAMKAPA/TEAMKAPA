"""Linie kropkowane (C.5).

Kropek szukamy w czulszej masce tuszu (``Prepared.light_ink``), bo przy niskim DPI kropka to jeden
szary piksel. Kropka to składowa spójna nie większa niż ``dot_max_mm`` w obu wymiarach. Kropki są
łączone w wiersze (środki w pionie w granicach ``dot_row_tolerance_mm``), a w wierszu w łańcuchy,
dopóki przerwa między sąsiednimi kropkami nie przekracza ``dot_max_gap_mm``. Łańcuch jest linią
kropkowaną, gdy ma ≥ ``dot_min_count`` kropek, rozpiętość ≥ ``dot_min_span_mm`` i regularne
odstępy (zmienność odstępów środków ≤ ``dot_spacing_cv``, a dla wielokropków „…” zmienność sum
trzech kolejnych odstępów ≤ ``dot_spacing_cv`` / 4). Kropki w tekście (kropki, kropki nad „i”)
nie tworzą tak długich, regularnych łańcuchów; szum skanu też nie.
"""

from dataclasses import dataclass

import cv2
import numpy as np

from backend.worker.vision.config import DetectConfig
from backend.worker.vision.detect.candidates import Candidate
from backend.worker.vision.detect.preprocess import Mask, PixelBox
from backend.worker.vision.detect.scale import Scale
from backend.worker.vision.models import ElementKind


@dataclass(frozen=True)
class Dot:
    box: PixelBox

    @property
    def cx(self) -> float:
        return (self.box[0] + self.box[2]) / 2

    @property
    def cy(self) -> float:
        return (self.box[1] + self.box[3]) / 2


def find_dots(ink: Mask, cfg: DetectConfig, scale: Scale) -> list[Dot]:
    limit = max(1.0, scale.px(cfg.dot_max_mm))
    count, _, stats, _ = cv2.connectedComponentsWithStats(ink.astype(np.uint8), connectivity=8)
    dots = []
    for label in range(1, count):
        x, y, w, h = (int(v) for v in stats[label, :4])
        if w <= limit and h <= limit:
            dots.append(Dot((x, y, x + w, y + h)))
    return dots


def _rows(dots: list[Dot], tolerance: float) -> list[list[Dot]]:
    rows: list[list[Dot]] = []
    for dot in sorted(dots, key=lambda d: (d.cy, d.cx)):
        if rows and abs(dot.cy - rows[-1][0].cy) <= tolerance:
            rows[-1].append(dot)
        else:
            rows.append([dot])
    return rows


def _chains(row: list[Dot], max_gap: float) -> list[list[Dot]]:
    chains: list[list[Dot]] = []
    for dot in sorted(row, key=lambda d: (d.cx, d.cy)):
        if chains and dot.box[0] - chains[-1][-1].box[2] <= max_gap:
            chains[-1].append(dot)
        else:
            chains.append([dot])
    return chains


def _cv(values: np.ndarray) -> float:
    mean = float(values.mean())
    return float(values.std()) / mean if mean > 0 else float("inf")


def _regular(chain: list[Dot], cv_limit: float) -> bool:
    """Odstępy środków regularne albo okresowe co 3 (wielokropki „…”: 6, 6, 18 px)."""
    steps = np.diff([d.cx for d in chain])
    if _cv(steps) <= cv_limit:
        return True
    triples = np.convolve(steps, np.ones(3), mode="valid")
    return triples.size > 0 and _cv(triples) <= cv_limit / 4


def dotted_lines(ink: Mask, cfg: DetectConfig, scale: Scale) -> list[Candidate]:
    tolerance = max(1.0, scale.px(cfg.dot_row_tolerance_mm))
    max_gap = scale.px(cfg.dot_max_gap_mm)
    min_span = scale.px(cfg.dot_min_span_mm)
    result = []
    for row in _rows(find_dots(ink, cfg, scale), tolerance):
        for chain in _chains(row, max_gap):
            if len(chain) < cfg.dot_min_count:
                continue
            box = (
                min(d.box[0] for d in chain),
                min(d.box[1] for d in chain),
                max(d.box[2] for d in chain),
                max(d.box[3] for d in chain),
            )
            if box[2] - box[0] < min_span or not _regular(chain, cfg.dot_spacing_cv):
                continue
            result.append(Candidate(kind=ElementKind.DOTTED_LINE, box=box, confidence=0.8))
    return sorted(result, key=lambda c: (c.box[1], c.box[0]))
