"""Determinizm, publiczne API detekcji i wejście kroku 3 (#10, C.8)."""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import cv2
import pytest

from backend.worker.pipeline.step03_visual_analyze import analyze_visual
from backend.worker.vision.config import RenderConfig
from backend.worker.vision.detect import detect_page, ipp_disabled
from backend.worker.vision.detect.page import detect_image
from backend.worker.vision.models import (
    DocumentKind,
    DpiSource,
    ElementKind,
    PageMeta,
    PageSource,
    RenderedPage,
)
from tests.vision.synth import SynthPage, encode, make_pdf
from tests.vision.synth_forms import make_form, rotate

REPO = Path(__file__).resolve().parents[2]
SCRIPT = """
import hashlib, json, sys
sys.path.insert(0, sys.argv[1])
from backend.worker.vision.detect.page import detect_image
from tests.vision.synth_forms import make_form, rotate
form = rotate(make_form(seed=3), 1.0)
result = detect_image(form.image, page=1, dpi=form.dpi)
payload = json.dumps([e.model_dump(mode="json") for e in result.elements], sort_keys=True)
print(hashlib.sha256(payload.encode()).hexdigest())
"""


def _digest() -> str:
    form = rotate(make_form(seed=3), 1.0)
    result = detect_image(form.image, page=1, dpi=form.dpi)
    payload = json.dumps([e.model_dump(mode="json") for e in result.elements], sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


def test_same_image_same_result_across_processes() -> None:
    expected = _digest()
    assert _digest() == expected
    for _ in range(2):
        output = subprocess.run(  # noqa: S603 - stały skrypt testowy, bez powłoki
            [sys.executable, "-c", SCRIPT, str(REPO)],
            capture_output=True,
            text=True,
            check=True,
            cwd=REPO,
        )
        assert output.stdout.strip() == expected


def test_ipp_is_disabled_during_detection_and_restored(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[bool] = []

    def spy(*args: object, **kwargs: object) -> object:
        seen.append(bool(cv2.ipp.useIPP()))
        return detect_image(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr("backend.worker.vision.detect.detect_image", spy)
    form = make_form(seed=1)
    meta = PageMeta(
        page=1,
        width_px=form.width,
        height_px=form.height,
        dpi=None,  # DPI z proporcji A4
        dpi_source=DpiSource.UNKNOWN,
        source=PageSource.IMAGE,
    )
    before = bool(cv2.ipp.useIPP())
    result = detect_page(RenderedPage(meta=meta, image=form.image))
    assert seen == [False]
    assert bool(cv2.ipp.useIPP()) == before
    assert result.dpi == pytest.approx(200.0, abs=0.5)
    with ipp_disabled():
        assert not cv2.ipp.useIPP()


def test_step03_on_png_and_pdf() -> None:
    form = make_form(seed=0)
    png = encode(form.image, "PNG", dpi=(200.0, 200.0))
    result = analyze_visual(png, DocumentKind.PNG, render_cfg=RenderConfig(), isolated=False)
    cells = sorted(e.cells or 0 for e in result.elements if e.kind is ElementKind.CHAR_BOXES)
    assert cells == [2, 3, 11]
    assert result.document.detected_kind is DocumentKind.PNG and len(result.pages) == 1

    black = (0, 0, 0)
    frame = (  # komórka 200 × 40 pt i checkbox 10 × 10 pt (linie 1 pt)
        (100.0, 700.0, 300.0, 701.0, black),
        (100.0, 739.0, 300.0, 740.0, black),
        (100.0, 700.0, 101.0, 740.0, black),
        (299.0, 700.0, 300.0, 740.0, black),
        (100.0, 600.0, 110.0, 601.0, black),
        (100.0, 609.0, 110.0, 610.0, black),
        (100.0, 600.0, 101.0, 610.0, black),
        (109.0, 600.0, 110.0, 610.0, black),
    )
    pdf = make_pdf([SynthPage(rects=frame), SynthPage()])
    result = analyze_visual(pdf, DocumentKind.PDF, render_cfg=RenderConfig(), isolated=False)
    assert [len(p.elements) for p in result.pages] == [2, 0]
    kinds = sorted(e.kind.value for e in result.elements)
    assert kinds == ["checkbox", "table_cell"]
    assert all(e.page == 1 and e.id.startswith("cv-p1-") for e in result.elements)
