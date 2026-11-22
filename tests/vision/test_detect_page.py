"""Złożenie detektorów, stabilne ID i pewność (#10, C.7)."""

import re

import pytest

from backend.worker.vision.detect.groups import GROUP_CANDIDATE
from backend.worker.vision.detect.page import PageDetection, detect_image
from backend.worker.vision.detect.preprocess import LOW_CONTRAST, SKEW_CORRECTED
from backend.worker.vision.models import ElementKind, ElementRole, ElementSource
from tests.vision.synth_forms import SynthForm, contrast, make_form, rotate

Detection = tuple[SynthForm, PageDetection]


@pytest.fixture(scope="module")
def detection() -> Detection:
    form = make_form(seed=0)
    return form, detect_image(form.image, page=2, dpi=form.dpi)


def test_ids_are_sequential_in_reading_order(detection: Detection) -> None:
    form, result = detection
    elements = result.elements
    assert [e.id for e in elements] == [f"cv-p2-{i:03d}" for i in range(1, len(elements) + 1)]
    assert all(re.fullmatch(r"cv-p2-\d{3}", e.id) and e.page == 2 for e in elements)
    rows = [int(e.bbox.y0 * form.height) // 16 for e in elements]  # id_row_mm=2 mm → 16 px
    assert rows == sorted(rows)
    assert all(e.source is ElementSource.OPENCV and e.role is ElementRole.UNKNOWN for e in elements)
    kinds = {e.kind for e in elements}
    assert kinds == {
        ElementKind.TABLE_CELL,
        ElementKind.SECTION_FRAME,
        ElementKind.CHAR_BOXES,
        ElementKind.CHECKBOX,
        ElementKind.CHECKBOX_GROUP,
        ElementKind.TEXT_LINE,
        ElementKind.DOTTED_LINE,
    }  # signature i office_only wymagają tekstu: OpenCV ich nie nadaje


def test_group_options_reference_checkboxes(detection: Detection) -> None:
    _, result = detection
    by_id = {e.id: e for e in result.elements}
    groups = [e for e in result.elements if e.kind is ElementKind.CHECKBOX_GROUP]
    assert len(groups) == 1
    group = groups[0]
    assert len(group.options) >= 2 and group.multi is None
    assert all(by_id[o].kind is ElementKind.CHECKBOX for o in group.options)
    assert GROUP_CANDIDATE in group.confidence_reasons and group.confidence < 0.5


def test_page_reasons_lower_confidence() -> None:
    form = make_form(seed=0)
    clean = detect_image(form.image, page=1, dpi=form.dpi)
    faded = detect_image(contrast(form, 0.4).image, page=1, dpi=form.dpi)
    skewed = detect_image(rotate(form, 1.5).image, page=1, dpi=form.dpi)
    assert clean.reasons == () and clean.angle == 0.0
    assert faded.reasons == (LOW_CONTRAST,)
    assert skewed.reasons == (SKEW_CORRECTED,) and skewed.angle == pytest.approx(1.5, abs=0.05)
    cell_clean = next(e for e in clean.elements if e.kind is ElementKind.TABLE_CELL)
    cell_faded = next(e for e in faded.elements if e.kind is ElementKind.TABLE_CELL)
    assert cell_faded.confidence == pytest.approx(cell_clean.confidence * 0.8)
    assert LOW_CONTRAST in cell_faded.confidence_reasons
    assert all(SKEW_CORRECTED in e.confidence_reasons for e in skewed.elements)


def test_char_boxes_cells(detection: Detection) -> None:
    _, result = detection
    cells = sorted(e.cells or 0 for e in result.elements if e.kind is ElementKind.CHAR_BOXES)
    assert cells == [2, 3, 11]
