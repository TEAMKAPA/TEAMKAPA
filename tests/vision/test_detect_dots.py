"""Linie kropkowane (#10, C.5)."""

import numpy as np
import pytest

from backend.worker.vision.config import DetectConfig
from backend.worker.vision.detect.dots import dotted_lines
from backend.worker.vision.detect.preprocess import prepare
from backend.worker.vision.detect.scale import Scale
from backend.worker.vision.models import ElementKind
from tests.vision.synth_forms import DEGRADATIONS, make_form

CFG = DetectConfig()
SCALE = Scale(200.0)


def _dots(ink: np.ndarray, y: int, xs: list[int], size: int = 2) -> None:
    for x in xs:
        ink[y : y + size, x : x + size] = True


def test_regular_dots_and_ellipsis() -> None:
    ink = np.zeros((300, 1200), dtype=bool)
    _dots(ink, 50, list(range(100, 600, 8)))  # kropki co 1 mm
    ellipsis = [x for start in range(100, 700, 30) for x in (start, start + 6, start + 12)]
    _dots(ink, 150, ellipsis)  # „…” co 3,8 mm
    found = [c.box for c in dotted_lines(ink, CFG, SCALE)]
    assert found == [(100, 50, 598, 52), (100, 150, 684, 152)]
    assert all(c.kind is ElementKind.DOTTED_LINE for c in dotted_lines(ink, CFG, SCALE))


def test_text_periods_noise_and_short_chains_are_ignored() -> None:
    ink = np.zeros((400, 1200), dtype=bool)
    _dots(ink, 50, [100, 180, 300, 420, 600])  # kropki w tekście: daleko od siebie
    rng = np.random.default_rng(0)
    for y, x in zip(rng.integers(100, 200, 300), rng.integers(0, 1200, 300), strict=True):
        ink[y, x] = True  # szum skanu
    _dots(ink, 250, list(range(100, 150, 8)))  # 7 kropek: za krótko
    irregular = [100, 104, 121, 123, 140, 158, 160, 175, 179, 199, 202, 220]
    _dots(ink, 320, irregular)  # nieregularne odstępy
    assert dotted_lines(ink, CFG, SCALE) == []


@pytest.mark.parametrize("name", ["clean", "rotate_1", "noise_20", "dpi_100"])
def test_synthetic_dotted_lines_and_signature(name: str) -> None:
    form = make_form(seed=4)
    degraded = form if name == "clean" else DEGRADATIONS[name](form)
    scale = Scale(degraded.dpi)
    prepared = prepare(degraded.image, CFG, scale)
    found = [prepared.to_page(c.box) for c in dotted_lines(prepared.light_ink, CFG, scale)]
    expected = [
        degraded.pixel_box(e.box)
        for e in degraded.elements
        if e.kind in {ElementKind.DOTTED_LINE, ElementKind.SIGNATURE}
    ]
    assert len(found) == len(expected)
    for box in expected:
        assert any(abs(box[0] - f[0]) <= 3 and abs(box[2] - f[2]) <= 3 for f in found), box
