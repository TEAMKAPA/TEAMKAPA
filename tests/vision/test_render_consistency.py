"""Spójność bbox z rendererem (wymaganie #12).

Syntetyczny PDF z kolorowymi prostokątami o znanych współrzędnych PDF jest renderowany w różnych
DPI, z różnym ``/Rotate``, przesuniętą MediaBox i CropBox. Bbox zmierzony z pikseli (maska koloru)
musi się zgadzać z ``coords.pdf_rect_to_norm`` w tolerancji 1,5 px przy danym DPI.

Generator PDF (``tests/vision/synth.py``) jest niezależny od PDFium, a ``pdf_rect_to_norm`` ma
osobne testy z wartościami wyliczonymi ręcznie, więc test nie potwierdza błędu samym sobą.
"""

import numpy as np
import pytest

from backend.worker.vision.config import RenderConfig
from backend.worker.vision.coords import norm_to_px, pdf_rect_to_norm, px_to_norm, to_bbox
from backend.worker.vision.models import ImageArray, PageGeometry
from backend.worker.vision.render.pdf import render_pdf
from tests.vision.synth import Color, PdfRect, SynthPage, make_pdf

TOLERANCE_PX = 1.5
RED, GREEN, BLUE = (255, 0, 0), (0, 255, 0), (0, 0, 255)


def _rects_inside(box: tuple[float, float, float, float]) -> tuple[PdfRect, ...]:
    """Trzy prostokąty w różnych miejscach widocznego obszaru (ułamki dobrane asymetrycznie)."""
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0

    def r(fx0: float, fy0: float, fx1: float, fy1: float, c: Color) -> PdfRect:
        return (x0 + fx0 * w, y0 + fy0 * h, x0 + fx1 * w, y0 + fy1 * h, c)

    return (
        r(0.05, 0.80, 0.30, 0.95, RED),  # przy lewym górnym rogu
        r(0.60, 0.10, 0.90, 0.25, GREEN),  # przy prawym dolnym rogu
        r(0.40, 0.45, 0.47, 0.70, BLUE),  # wąski, pośrodku
    )


LAYOUTS = {
    "a4": ((0.0, 0.0, 595.0, 842.0), None),
    "offset_mediabox": ((100.0, 200.0, 700.0, 1000.0), None),
    "smaller_cropbox": ((0.0, 0.0, 600.0, 800.0), (100.0, 100.0, 500.0, 700.0)),
    "cropbox_beyond_mediabox": ((0.0, 0.0, 600.0, 800.0), (-50.0, 100.0, 650.0, 900.0)),
}


def _measure(image: ImageArray, color: Color) -> tuple[float, float, float, float]:
    """Bbox (krawędzie pikseli) pikseli w pełni zabarwionych danym kolorem."""
    on = [image[:, :, i] > 200 if v == 255 else image[:, :, i] < 55 for i, v in enumerate(color)]
    ys, xs = np.nonzero(on[0] & on[1] & on[2])
    assert xs.size > 0, f"nie znaleziono koloru {color}"
    return (float(xs.min()), float(ys.min()), float(xs.max() + 1), float(ys.max() + 1))


@pytest.mark.parametrize("dpi", [72, 150, 200, 300])
@pytest.mark.parametrize("rotate", [0, 90, 180, 270])
@pytest.mark.parametrize("layout", sorted(LAYOUTS))
def test_rendered_bbox_matches_pdf_coordinates(dpi: int, rotate: int, layout: str) -> None:
    mediabox, cropbox = LAYOUTS[layout]
    geometry = PageGeometry(mediabox=mediabox, cropbox=cropbox or mediabox, rotation=rotate)
    rects = _rects_inside(geometry.visible_box)
    data = make_pdf([SynthPage(mediabox=mediabox, cropbox=cropbox, rotate=rotate, rects=rects)])

    page = render_pdf(data, RenderConfig(dpi=dpi), isolated=False).pages[0]
    assert page.meta.geometry == geometry
    w, h = page.meta.width_px, page.meta.height_px
    exp_w, exp_h = (v * dpi / 72 for v in geometry.displayed_size_pt)
    assert abs(w - exp_w) <= 1 and abs(h - exp_h) <= 1

    for x0, y0, x1, y1, color in rects:
        expected = norm_to_px(to_bbox(pdf_rect_to_norm((x0, y0, x1, y1), geometry)), w, h)
        measured = _measure(page.image, color)
        diff = max(abs(a - b) for a, b in zip(measured, expected, strict=True))
        assert diff <= TOLERANCE_PX, f"{color}: zmierzono {measured}, oczekiwano {expected}"
        # ten sam prostokąt w ułamkach strony nie zależy od DPI (tolerancja w ułamkach)
        got = to_bbox(px_to_norm(measured, w, h))
        want = to_bbox(pdf_rect_to_norm((x0, y0, x1, y1), geometry))
        assert abs(got.x0 - want.x0) <= TOLERANCE_PX / w
        assert abs(got.y1 - want.y1) <= TOLERANCE_PX / h


def test_known_pixels_at_72_dpi_rotate_90() -> None:
    """Punkt kontrolny wyliczony ręcznie: prostokąt z lewego górnego rogu A4 po obrocie o 90°."""
    data = make_pdf([SynthPage(rotate=90, rects=((59.5, 742.0, 119.0, 842.0, RED),))])
    page = render_pdf(data, RenderConfig(dpi=72), isolated=False).pages[0]
    assert (page.meta.width_px, page.meta.height_px) == (842, 595)
    measured = _measure(page.image, RED)
    # x: 842 - 100 .. 842; y: 59,5 .. 119 (krawędź 59,5 wypada w połowie piksela)
    assert measured[0] == 742 and measured[2] == 842
    assert 59 <= measured[1] <= 60 and measured[3] == 119
