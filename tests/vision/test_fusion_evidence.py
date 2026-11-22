"""Etap E.3: kontrola pikseli elementów vlm-* (ślad, wiersz tekstu, pusty obszar)."""

from functools import cache

import pytest

from backend.shared.schemas.quality import BoundingBox
from backend.worker.vision.detect.page import detect_image
from backend.worker.vision.fusion import codes
from backend.worker.vision.fusion.elements import PageFusionInput, fuse_page
from backend.worker.vision.fusion.evidence import PageEvidence, is_filler, make_verifier
from backend.worker.vision.models import ElementKind, ElementRole, ElementSource, VisualElement
from backend.worker.vision.vlm.roles import TextBlock
from tests.vision.synth_forms import TEXT_GRAY, SynthForm, _Canvas, blur, contrast, make_form

DPI = 200.0


def added(number: int, kind: ElementKind, box: tuple[float, float, float, float]) -> VisualElement:
    return VisualElement(
        id=f"vlm-p1-{number:03d}",
        page=1,
        kind=kind,
        bbox=BoundingBox(x0=box[0], y0=box[1], x1=box[2], y1=box[3]),
        cells=5 if kind is ElementKind.CHAR_BOXES else None,
        role=ElementRole.CHOICE if kind is ElementKind.CHECKBOX else ElementRole.INPUT,
        source=ElementSource.VLM,
        confidence=0.3,
        confidence_reasons=("VLM_ONLY_ELEMENT",),
    )


class Page:
    """Strona testowa: elementy i „tekst” w znanych miejscach (ułamki strony)."""

    def __init__(self) -> None:
        c = _Canvas(DPI, seed=0)
        self.canvas = c
        x = c.px(30)
        # (a) krótka linia kropek: 7 kropek na 6 mm, poniżej progów detektora (8 kropek, 10 mm)
        c.dotted_line(x, c.px(30), x + c.px(7))
        self.short_dots = c.frac(x, c.px(30) - 2, x + c.px(7), c.px(30) + 4)
        # (a) linia ciągła 60 mm
        c.text_line(x, c.px(50), x + c.px(60))
        self.line = c.frac(x, c.px(50) - 3, x + c.px(60), c.px(50) + 5)
        # (a) kratki 5 × 5 mm
        bx = c.char_boxes(x, c.px(70), 5)
        self.boxes = c.frac(*bx)
        # (a) checkbox 3,5 mm
        _, cb = c.checkbox(x, c.px(90))
        self.checkbox = c.frac(*cb)
        # (b) wiersz tekstu (pasek) z blokiem tekstu
        tb = c.text_bar(x, c.px(110), 80)
        self.text_row = c.frac(tb[0], tb[1] + 4, tb[2], tb[3] - 4)
        self.text_block = c.frac(*tb)
        # (b) wiersz „liter” bez bloku tekstu (np. skan bez OCR): 12 znaków 1,4 × 2,6 mm
        gy = c.px(130)
        for i in range(12):
            gx = x + c.px(2.2 * i)
            c.fill(gx, gy, gx + c.px(1.4), gy + c.px(2.6), value=TEXT_GRAY)
        self.glyph_row = c.frac(x, gy + 4, x + c.px(2.2 * 12), gy + c.px(2.6) - 4)
        # (c) pusty obszar
        self.blank = c.frac(x, c.px(160), x + c.px(60), c.px(162))
        # blok z samych kropek (wypełniacz, np. z warstwy tekstowej PDF) nad pustym obszarem
        self.filler_block = c.frac(x, c.px(159), x + c.px(60), c.px(163))
        self.image = c.image

    def blocks(self) -> list[TextBlock]:
        def tb(identifier: str, text: str, box: tuple[float, float, float, float]) -> TextBlock:
            return TextBlock(
                id=identifier,
                page=1,
                text=text,
                bbox=BoundingBox(x0=box[0], y0=box[1], x1=box[2], y1=box[3]),
            )

        return [
            tb("p1-b1", "Oświadczam, że dane są prawdziwe", self.text_block),
            tb("p1-b2", "..............................", self.filler_block),
        ]


@cache
def page() -> Page:
    return Page()


def verify(element: VisualElement, *, blocks: bool = True):  # type: ignore[no-untyped-def]
    p = page()
    return PageEvidence(p.image, 1, DPI, p.blocks() if blocks else []).verify(element)


def test_detector_misses_the_short_dotted_line() -> None:
    detected = detect_image(page().image, 1, DPI).elements
    assert not any(e.kind is ElementKind.DOTTED_LINE for e in detected)


@pytest.mark.parametrize(
    ("kind", "attr", "reason"),
    [
        (ElementKind.DOTTED_LINE, "short_dots", "DOTS"),
        (ElementKind.TEXT_LINE, "line", "LINE"),
        (ElementKind.SIGNATURE, "line", "LINE"),
        (ElementKind.CHAR_BOXES, "boxes", "STROKES"),
        (ElementKind.CHECKBOX, "checkbox", "BOX"),
    ],
)
def test_evidence_matching_kind_keeps_element(kind: ElementKind, attr: str, reason: str) -> None:
    verdict = verify(added(1, kind, getattr(page(), attr)))
    assert (verdict.keep, verdict.code, verdict.reason) == (
        True,
        codes.WEAK_VISUAL_EVIDENCE,
        reason,
    )


def test_text_row_with_block_is_rejected() -> None:
    verdict = verify(added(1, ElementKind.DOTTED_LINE, page().text_row))
    assert (verdict.keep, verdict.code, verdict.reason) == (
        False,
        codes.VLM_ONLY_REJECTED,
        "TEXT_ROW",
    )
    assert "tekst" in verdict.detail


def test_glyph_row_without_blocks_is_rejected() -> None:
    verdict = verify(added(1, ElementKind.DOTTED_LINE, page().glyph_row), blocks=False)
    assert (verdict.keep, verdict.reason) == (False, "TEXT_ROW")
    assert verdict.detail.startswith("znaki")


def test_blank_area_is_kept_without_evidence() -> None:
    verdict = verify(added(1, ElementKind.TEXT_LINE, page().blank))
    assert (verdict.keep, verdict.code, verdict.reason) == (True, codes.NO_VISUAL_EVIDENCE, "BLANK")


def test_filler_block_is_not_text() -> None:
    assert is_filler("..........") and is_filler("___ ___") and is_filler("")
    assert not is_filler("Imię i nazwisko ......")
    # Blok-wypełniacz nad pustym obszarem nie czyni z niego wiersza tekstu.
    assert verify(added(1, ElementKind.DOTTED_LINE, page().blank)).keep


def test_kind_mismatch_falls_back_to_text_rule() -> None:
    # Kratki w miejscu linii ciągłej: brak kresek pionowych, brak tekstu → tusz bez struktury.
    verdict = verify(added(1, ElementKind.CHAR_BOXES, page().line))
    assert verdict.keep and verdict.code == codes.NO_VISUAL_EVIDENCE


def test_wrong_page_is_error() -> None:
    element = added(1, ElementKind.TEXT_LINE, page().line)
    with pytest.raises(ValueError, match="nie należy"):
        PageEvidence(page().image, 2, DPI, []).verify(element)


def test_true_omissions_on_faded_page_survive() -> None:
    """OpenCV gubi kropki na wyblakłym i rozmytym druku; dodane przez VLM w ich miejscu zostają."""
    form: SynthForm = blur(contrast(make_form(seed=0), 0.35), 1.5)
    detected = detect_image(form.image, 1, form.dpi).elements
    truth = [e for e in form.elements if e.kind is ElementKind.DOTTED_LINE]
    assert truth and not any(e.kind is ElementKind.DOTTED_LINE for e in detected)
    candidates = tuple(added(i, ElementKind.DOTTED_LINE, t.box) for i, t in enumerate(truth, 1))
    verifier = make_verifier(form.image, 1, form.dpi, [])
    result = fuse_page(PageFusionInput(1, candidates, "ok", verify=verifier), [])
    assert result.rejected == ()
    assert len(result.elements) == len(truth)
    assert all("VLM_ONLY_ELEMENT" in e.confidence_reasons for e in result.elements)


def test_fusion_with_verifier_removes_text_row_only() -> None:
    p = page()
    elements = (
        added(1, ElementKind.DOTTED_LINE, p.short_dots),
        added(2, ElementKind.DOTTED_LINE, p.text_row),
        added(3, ElementKind.TEXT_LINE, p.blank),
    )
    verifier = make_verifier(p.image, 1, DPI, p.blocks())
    result = fuse_page(PageFusionInput(1, elements, "ok", verify=verifier), p.blocks())
    assert [e.id for e in result.elements] == ["vlm-p1-001", "vlm-p1-003"]
    assert [r.element.id for r in result.rejected] == ["vlm-p1-002"]
    assert [r.code for r in result.review] == [codes.VLM_ONLY_REJECTED]


def test_verification_is_deterministic() -> None:
    p = page()
    elements = [
        added(1, ElementKind.DOTTED_LINE, p.short_dots),
        added(2, ElementKind.DOTTED_LINE, p.text_row),
    ]
    first = [PageEvidence(p.image, 1, DPI, p.blocks()).verify(e) for e in elements]
    second = [PageEvidence(p.image.copy(), 1, DPI, p.blocks()).verify(e) for e in elements]
    assert first == second


def test_few_dots_do_not_confirm_a_wide_dotted_line() -> None:
    """Kilka kropek (np. w wierszu tekstu) nie potwierdza linii na dużo większą szerokość."""
    x0, y0, _, y1 = page().short_dots
    wide = added(1, ElementKind.DOTTED_LINE, (x0, y0, x0 + 0.3, y1))
    verdict = verify(wide)
    assert verdict.keep and verdict.code == codes.NO_VISUAL_EVIDENCE
