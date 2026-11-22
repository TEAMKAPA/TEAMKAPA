"""Kandydaci na grupy wyboru (#10, C.6)."""

import numpy as np

from backend.worker.vision.config import DetectConfig
from backend.worker.vision.detect.candidates import Candidate
from backend.worker.vision.detect.groups import GROUP_CANDIDATE, checkbox_groups
from backend.worker.vision.detect.lines import find_lines
from backend.worker.vision.detect.preprocess import PixelBox, prepare
from backend.worker.vision.detect.rects import classify_rects, find_rects
from backend.worker.vision.detect.scale import Scale
from backend.worker.vision.models import ElementKind
from tests.vision.synth_forms import make_form

CFG = DetectConfig()
SCALE = Scale(200.0)


def _checkbox(ink: np.ndarray, x: int, y: int, size: int = 24) -> PixelBox:
    ink[y : y + 2, x : x + size] = ink[y + size - 2 : y + size, x : x + size] = True
    ink[y : y + size, x : x + 2] = ink[y : y + size, x + size - 2 : x + size] = True
    return (x, y, x + size, y + size)


def _groups(ink: np.ndarray, exclude: list[PixelBox] | None = None) -> list[Candidate]:
    lines = find_lines(ink, CFG, SCALE)
    found = classify_rects(find_rects(lines.structure, CFG, SCALE), CFG, SCALE)
    return checkbox_groups(found, ink, lines, CFG, SCALE, exclude or [])


def test_row_group_with_option_texts() -> None:
    ink = np.zeros((300, 1400), dtype=bool)
    members = [_checkbox(ink, x, 100) for x in (100, 400, 700)]
    for x in (100, 400, 700):
        ink[106:118, x + 40 : x + 160] = True  # tekst opcji
    groups = _groups(ink)
    assert len(groups) == 1
    group = groups[0]
    assert group.kind is ElementKind.CHECKBOX_GROUP and group.members == tuple(members)
    assert group.box == (100, 100, 860, 124)
    assert group.confidence < 0.5 and group.reasons == (GROUP_CANDIDATE,)


def test_column_group_text_stops_at_dots_and_vertical_line() -> None:
    ink = np.zeros((600, 1400), dtype=bool)
    members = [_checkbox(ink, 100, y) for y in (100, 163, 226)]
    ink[106:118, 140:300] = True  # tekst pierwszej opcji
    dots = (310, 114, 700, 116)
    ink[114:116, 310:700:8] = True  # kropki za tekstem (wykluczone przez wywołującego)
    ink[169:181, 140:250] = True
    ink[150:250, 260:262] = True  # linia pionowa (krawędź komórki)
    ink[169:181, 270:500] = True  # tekst za linią: nie należy do opcji
    groups = _groups(ink, [dots])
    assert [g.members for g in groups] == [tuple(members)]
    assert groups[0].box == (100, 100, 300, 250)


def test_single_and_row_first() -> None:
    ink = np.zeros((500, 1400), dtype=bool)
    _checkbox(ink, 100, 400)  # samotny checkbox
    row = [_checkbox(ink, x, 100) for x in (100, 300)]
    below = _checkbox(ink, 100, 170)  # w kolumnie z pierwszym checkboxem wiersza
    groups = _groups(ink)
    assert [g.members for g in groups] == [tuple(row)]
    assert all(below not in g.members for g in groups)


def test_synthetic_form_group_members() -> None:
    form = make_form(seed=2)
    prepared = prepare(form.image, CFG, SCALE)
    groups = _groups(prepared.ink)
    gt = next(e for e in form.elements if e.kind is ElementKind.CHECKBOX_GROUP)
    expected = tuple(form.pixel_box(form.elements[m].box) for m in gt.members)
    assert [g.members for g in groups] == [expected]
    assert groups[0].box == form.pixel_box(gt.box)
