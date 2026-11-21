"""Renderer PDF: metadane, determinizm, błędy dokumentu, limity (PDF-y syntetyczne)."""

import hashlib

import numpy as np
import pytest

from backend.shared.schemas.quality import Severity
from backend.worker.vision.config import RenderConfig
from backend.worker.vision.errors import IssueCode, RenderError
from backend.worker.vision.models import DpiSource, PageSource
from backend.worker.vision.render.pdf import MIN_DPI, effective_dpi, render_pdf
from tests.vision.synth import SynthPage, make_pdf

RED = (255, 0, 0)
TWO_PAGES = make_pdf(
    [
        SynthPage(rects=((59.5, 742.0, 119.0, 842.0, RED),)),
        SynthPage(mediabox=(0, 0, 842, 595), rotate=90),
    ]
)


def _digest(image: np.ndarray) -> str:
    return hashlib.sha256(image.tobytes()).hexdigest()


def test_page_metadata() -> None:
    result = render_pdf(TWO_PAGES, RenderConfig(dpi=200), isolated=False)
    assert result.info.page_count == 2
    assert set(result.info.renderer_versions) == {"pypdfium2", "pdfium"}
    first, second = (p.meta for p in result.pages)
    assert (first.page, second.page) == (1, 2)
    assert first.dpi == 200.0 and first.dpi_source is DpiSource.RENDER
    assert first.source is PageSource.NATIVE_PDF and first.issues == ()
    assert first.geometry is not None and first.geometry.mediabox == (0.0, 0.0, 595.0, 842.0)
    # rozmiar w pikselach = rozmiar wyświetlany * DPI / 72 (z dokładnością zaokrąglenia)
    assert abs(first.width_px - 595 * 200 / 72) <= 1 and abs(first.height_px - 842 * 200 / 72) <= 1
    # strona pozioma obrócona o 90 stopni jest wyświetlana pionowo
    assert second.rotation == 90
    assert (
        abs(second.width_px - 595 * 200 / 72) <= 1 and abs(second.height_px - 842 * 200 / 72) <= 1
    )


def test_rendering_is_deterministic_and_isolation_is_transparent() -> None:
    cfg = RenderConfig(dpi=150)
    a = render_pdf(TWO_PAGES, cfg, isolated=False)
    b = render_pdf(TWO_PAGES, cfg, isolated=False)
    c = render_pdf(TWO_PAGES, cfg)  # proces potomny
    digests = [[_digest(p.image) for p in r.pages] for r in (a, b, c)]
    assert digests[0] == digests[1] == digests[2]
    assert [p.meta for p in a.pages] == [p.meta for p in c.pages]


def test_image_is_rgb_uint8_with_white_background() -> None:
    page = render_pdf(make_pdf([SynthPage()]), RenderConfig(dpi=72), isolated=False).pages[0]
    assert page.image.dtype == np.uint8 and page.image.shape == (842, 595, 3)
    assert page.image.flags["C_CONTIGUOUS"]
    assert int(page.image.min()) == 255


def test_converted_source_is_kept() -> None:
    result = render_pdf(TWO_PAGES, RenderConfig(dpi=72), PageSource.CONVERTED, isolated=False)
    assert {p.meta.source for p in result.pages} == {PageSource.CONVERTED}
    with pytest.raises(ValueError, match="image"):
        render_pdf(TWO_PAGES, RenderConfig(dpi=72), PageSource.IMAGE, isolated=False)


def test_render_without_forms_works() -> None:
    result = render_pdf(TWO_PAGES, RenderConfig(dpi=72, render_forms=False), isolated=False)
    assert len(result.pages) == 2


@pytest.mark.parametrize("isolated", [False, True])
@pytest.mark.parametrize(
    ("data", "code"),
    [
        (make_pdf([SynthPage()], user_password="tajne"), IssueCode.PDF_ENCRYPTED),
        (b"%PDF-1.4\nto nie jest PDF", IssueCode.PDF_UNREADABLE),
        (make_pdf([]), IssueCode.PDF_UNREADABLE),
    ],
)
def test_document_errors(data: bytes, code: IssueCode, isolated: bool) -> None:
    with pytest.raises(RenderError) as info:
        render_pdf(data, RenderConfig(), isolated=isolated)
    assert info.value.code is code


@pytest.mark.parametrize("isolated", [False, True])
def test_too_many_pages(isolated: bool) -> None:
    data = make_pdf([SynthPage()] * 3)
    with pytest.raises(RenderError) as info:
        render_pdf(data, RenderConfig(max_pages=2), isolated=isolated)
    assert info.value.code is IssueCode.TOO_MANY_PAGES
    assert info.value.detail == "pages=3 limit=2"
    assert len(render_pdf(data, RenderConfig(max_pages=3), isolated=isolated).pages) == 3


def test_dpi_reduced_when_page_exceeds_pixel_limit() -> None:
    cfg = RenderConfig(dpi=200, max_page_pixels=1_000_000)  # A4 w 200 DPI to ok. 3,87 Mpx
    page = render_pdf(make_pdf([SynthPage()]), cfg, isolated=False).pages[0]
    [issue] = page.meta.issues
    assert issue.code is IssueCode.DPI_REDUCED and issue.severity is Severity.WARNING
    assert issue.detail == f"dpi 200->{int(page.meta.dpi or 0)}"
    assert page.meta.dpi is not None and page.meta.dpi < 200
    assert page.meta.width_px * page.meta.height_px <= 1_000_000


def test_effective_dpi() -> None:
    cfg = RenderConfig(dpi=200, max_page_pixels=1_000_000)
    assert effective_dpi(72, 72, cfg) == (200, None)  # 1 cal x 1 cal = 40 000 px
    dpi, issue = effective_dpi(595, 842, cfg)
    assert dpi == 101 and issue is not None  # floor(200 * sqrt(1e6 / 3 868 133))
    with pytest.raises(RenderError) as info:
        effective_dpi(14_400, 14_400, cfg)  # 200 x 200 cali wymagałoby DPI < MIN_DPI
    assert info.value.code is IssueCode.IMAGE_TOO_LARGE
    assert MIN_DPI == 36
