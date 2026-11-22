"""Prostokąty: checkboxy, kratki, komórki, ramki sekcji (#10, C.4)."""

import cv2
import numpy as np
import pytest

from backend.worker.vision.config import DetectConfig
from backend.worker.vision.detect.candidates import Candidate
from backend.worker.vision.detect.lines import find_lines
from backend.worker.vision.detect.preprocess import Mask, PixelBox, prepare
from backend.worker.vision.detect.rects import (
    CELLS_IRREGULAR,
    classify_rects,
    find_rects,
    find_sections,
)
from backend.worker.vision.detect.scale import Scale
from backend.worker.vision.models import ElementKind
from tests.vision.synth_forms import make_form

CFG = DetectConfig()
SCALE = Scale(200.0)


def _frame(ink: Mask, box: PixelBox, width: int = 2) -> None:
    x0, y0, x1, y1 = box
    ink[y0 : y0 + width, x0:x1] = True
    ink[y1 - width : y1, x0:x1] = True
    ink[y0:y1, x0 : x0 + width] = True
    ink[y0:y1, x1 - width : x1] = True


def _detect(ink: Mask) -> list[Candidate]:
    structure = find_lines(ink, CFG, SCALE).structure
    found = classify_rects(find_rects(structure, CFG, SCALE), CFG, SCALE)
    return found + find_sections(structure, found, CFG, SCALE)


def _kinds(found: list[Candidate]) -> list[tuple[ElementKind, PixelBox, int | None]]:
    return sorted(((c.kind, c.box, c.cells) for c in found), key=lambda t: (t[1][1], t[1][0]))


def test_checkbox_glyph_with_shadow_and_letter_loop() -> None:
    ink = np.zeros((300, 400), dtype=bool)
    _frame(ink, (50, 50, 78, 78))  # 3,5 mm
    ink[100:120, 50:52] = ink[100:102, 50:70] = True  # glif ❑: cienka lewa i górna krawędź,
    ink[100:122, 68:72] = ink[118:122, 50:72] = True  # gruba prawa i dolna (cień)
    cv2.ellipse(ink.view(np.uint8), (250, 150), (14, 18), 0, 0, 360, 1, 3)  # pętla litery „O”
    assert _kinds(_detect(ink)) == [
        (ElementKind.CHECKBOX, (50, 50, 78, 78), None),
        (ElementKind.CHECKBOX, (50, 100, 72, 122), None),
    ]


def test_char_boxes_groups_and_irregular_cells() -> None:
    ink = np.zeros((300, 900), dtype=bool)
    for i in range(11):  # PESEL: 11 kratek po 40 px ze wspólnymi krawędziami
        _frame(ink, (50 + 40 * i, 50, 92 + 40 * i, 92))
    for x in (50, 90):  # kod pocztowy: 2 + 3 kratki rozdzielone myślnikiem
        _frame(ink, (x, 150, x + 42, 192))
    ink[170:172, 140:150] = True
    for x in (160, 200, 240):
        _frame(ink, (x, 150, x + 42, 192))
    _frame(ink, (50, 230, 92, 272))  # kratki o różnej szerokości
    _frame(ink, (90, 230, 152, 272))  # 62 px: proporcja 1,5, szerokości różne o 48%
    found = _kinds(_detect(ink))
    assert found == [
        (ElementKind.CHAR_BOXES, (50, 50, 492, 92), 11),
        (ElementKind.CHAR_BOXES, (50, 150, 132, 192), 2),
        (ElementKind.CHAR_BOXES, (160, 150, 282, 192), 3),
        (ElementKind.CHAR_BOXES, (50, 230, 152, 272), 2),
    ]
    irregular = next(c for c in _detect(ink) if c.box == (50, 230, 152, 272))
    assert irregular.reasons == (CELLS_IRREGULAR,) and irregular.confidence < 0.9


def test_cells_with_text_and_section_frame_with_gap() -> None:
    ink = np.zeros((600, 1200), dtype=bool)
    _frame(ink, (100, 100, 1100, 160))
    _frame(ink, (100, 158, 500, 220))
    _frame(ink, (498, 158, 1100, 220))
    ink[120:136, 120:400] = True  # „tekst” w komórce
    _frame(ink, (88, 88, 1112, 232), width=4)  # ramka sekcji 8 px od tabeli
    _frame(ink, (5, 5, 1195, 595))  # obramowanie strony: pomijane
    found = _kinds(_detect(ink))
    assert found == [
        (ElementKind.SECTION_FRAME, (88, 88, 1112, 232), None),
        (ElementKind.TABLE_CELL, (100, 100, 1100, 160), None),
        (ElementKind.TABLE_CELL, (100, 158, 500, 220), None),
        (ElementKind.TABLE_CELL, (498, 158, 1100, 220), None),
    ]


def test_open_border_is_not_a_rectangle() -> None:
    ink = np.zeros((300, 600), dtype=bool)
    _frame(ink, (100, 100, 400, 200))
    ink[100:200, 398:400] = False  # brak prawej krawędzi
    assert _detect(ink) == []


@pytest.mark.parametrize("seed", [0, 3])
def test_synthetic_form_rectangles(seed: int) -> None:
    form = make_form(seed=seed)
    found = _detect(prepare(form.image, CFG, SCALE).ink)
    expected = {
        (e.kind, form.pixel_box(e.box), e.cells)
        for e in form.elements
        if e.kind
        in {
            ElementKind.CHECKBOX,
            ElementKind.CHAR_BOXES,
            ElementKind.TABLE_CELL,
            ElementKind.SECTION_FRAME,
        }
    }
    office = {
        (ElementKind.TABLE_CELL, form.pixel_box(e.box), None)
        for e in form.elements
        if e.kind is ElementKind.OFFICE_ONLY  # bez tekstu: ramka urzędu to komórka
    }
    got = {(c.kind, c.box, c.cells) for c in found}
    tolerance = 2
    for kind, box, cells in expected | office:
        assert any(
            k is kind
            and c == cells
            and all(abs(a - b) <= tolerance for a, b in zip(b2, box, strict=True))
            for k, b2, c in got
        ), (kind, box)
    assert len(got) == len(expected | office)


def test_thin_lines_with_missing_corner_pixel() -> None:
    """Linie 1 px z przerwą w narożniku (antyaliasing w obrazach PNG z COI) zamykają komórkę."""
    ink = np.zeros((300, 600), dtype=bool)
    ink[100, 100:500] = True  # góra
    ink[101:159, 100] = True  # lewa: kończy się piksel przed dolną linią
    ink[101:159, 499] = True
    ink[160, 101:500] = True  # dół: zaczyna się piksel na prawo od lewej linii
    ink[159, 499] = True
    assert _kinds(_detect(ink)) == [(ElementKind.TABLE_CELL, (100, 100, 500, 161), None)]
