"""Linie (#10, C.3)."""

import numpy as np

from backend.worker.vision.config import DetectConfig
from backend.worker.vision.detect.lines import find_lines, text_line_candidates
from backend.worker.vision.detect.preprocess import prepare
from backend.worker.vision.detect.scale import Scale
from backend.worker.vision.models import ElementKind
from tests.vision.synth_forms import make_form

CFG = DetectConfig()
SCALE = Scale(200.0)
RECT_KINDS = {
    ElementKind.TABLE_CELL,
    ElementKind.CHAR_BOXES,
    ElementKind.CHECKBOX,
    ElementKind.SECTION_FRAME,
    ElementKind.OFFICE_ONLY,
}


def test_text_line_on_synthetic_page() -> None:
    form = make_form(seed=2)
    ink = prepare(form.image, CFG, SCALE).ink
    lines = find_lines(ink, CFG, SCALE)
    rects = [form.pixel_box(e.box) for e in form.elements if e.kind in RECT_KINDS]
    found = text_line_candidates(lines, rects, CFG, SCALE)
    expected = [form.pixel_box(e.box) for e in form.elements if e.kind is ElementKind.TEXT_LINE]
    assert found == expected
    assert lines.v_segments  # pionowe krawędzie tabeli
    assert not (ink & ~lines.structure).any()


def test_separator_bar_short_line_and_bridged_gap() -> None:
    ink = np.zeros((400, 1000), dtype=bool)
    ink[50:56, 100:900] = True  # pasek 6 px: separator
    ink[150:152, 100:180] = True  # 80 px < 15 mm: za krótka
    ink[250:252, 100:400] = True  # linia z przerwą 3 px
    ink[250:252, 403:700] = True
    lines = find_lines(ink, CFG, SCALE)
    found = text_line_candidates(lines, [], CFG, SCALE)
    assert found == [(100, 250, 400, 252), (403, 250, 700, 252)]
    assert lines.structure[250, 401] and lines.structure[251, 402]  # przerwa domknięta
    border = text_line_candidates(lines, [(90, 248, 710, 300)], CFG, SCALE)
    assert border == []  # krawędź prostokąta
