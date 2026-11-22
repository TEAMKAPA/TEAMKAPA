"""Kontrola pikseli elementów dodanych przez VLM (``vlm-*``, E.3).

Zasada: **dodanie przez VLM musi mieć ślad w pikselach albo przynajmniej mu nie przeczyć.**
W ramce elementu (z marginesem ``evidence_margin_mm``, bo ramki VLM są niedokładne) szukamy
śladu zgodnego z kategorią, progami detektorów złagodzonymi o połowę (``FusionConfig``):

- ``dotted_line``: łańcuch regularnych kropek (ten sam detektor kropek z Etapu C, czulsza maska,
  ≥ ``evidence_dot_min_count`` kropek na ≥ ``evidence_dot_min_span_mm``), obejmujący
  ≥ ``evidence_line_share`` szerokości ramki,
- ``text_line``, ``table_cell`` i podpis: odcinek poziomy ≥ ``evidence_line_share`` szerokości ramki
  (podpis także kropki),
- ``char_boxes``: ≥ 2 kreski pionowe ≥ ``evidence_stroke_share`` wysokości ramki,
- ``checkbox`` i grupa: ≥ 2 kreski pionowe i ≥ 2 poziome (zamknięty kwadrat).

Wynik (``elements.Verdict``):

- (a) ślad zgodny z kategorią → zostaje, ``WEAK_VISUAL_EVIDENCE`` (LOW, jak każde ``vlm-*``),
- (b) brak śladu, a ramka leży na wierszu tekstu → **usunięty**, w przeglądzie ``VLM_ONLY_REJECTED``
  (powód ``TEXT_ROW``). Wiersz tekstu to: bloki tekstu zajmujące ≥ ``evidence_text_cover``
  szerokości ramki w jej pasie (bloki złożone głównie z kropek i podkreślników to miejsca do
  wpisania, nie tekst) albo ≥ ``evidence_glyph_count`` składowych tuszu wielkości litery,
- (c) brak śladu w pustym obszarze (tusz < ``evidence_ink_blank``) → zostaje, ``NO_VISUAL_EVIDENCE``
  (miejsce na wpis bez linii); tusz bez struktury i bez cech tekstu też zostaje (``INK``).

Prawdziwe pominięcie OpenCV daje (a) albo (c), więc zawsze zostaje jako LOW i trafia do przeglądu.
Kontrola działa tylko na obrazie strony i blokach tekstu, deterministycznie (IPP wyłączone).
"""

import re
from collections.abc import Sequence
from functools import cached_property

import cv2
import numpy as np

from backend.worker.vision.config import DetectConfig
from backend.worker.vision.detect import ipp_disabled
from backend.worker.vision.detect.dots import dotted_lines
from backend.worker.vision.detect.lines import components
from backend.worker.vision.detect.preprocess import Gray, Mask, PixelBox, binarize, to_gray
from backend.worker.vision.detect.scale import Scale
from backend.worker.vision.fusion import codes
from backend.worker.vision.fusion.config import FusionConfig
from backend.worker.vision.fusion.elements import Verdict, Verifier
from backend.worker.vision.models import ElementKind, ImageArray, VisualElement
from backend.worker.vision.vlm.roles import TextBlock

FILLER_CHARS = re.compile(r"[\s.…·_\-–—:]")

LINE_KINDS = frozenset({ElementKind.TEXT_LINE, ElementKind.TABLE_CELL, ElementKind.OFFICE_ONLY})
BOX_KINDS = frozenset({ElementKind.CHECKBOX, ElementKind.CHECKBOX_GROUP})


def _open(mask: Mask, size: tuple[int, int]) -> Mask:
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, size)
    opened = cv2.morphologyEx(mask.astype(np.uint8), cv2.MORPH_OPEN, kernel)
    return np.asarray(opened, dtype=np.uint8) > 0


def is_filler(text: str) -> bool:
    """Blok złożony głównie (≥ 50%) z kropek, podkreślników i odstępów: miejsce do wpisania."""
    stripped = text.strip()
    if not stripped:
        return True
    return len(FILLER_CHARS.findall(stripped)) >= 0.5 * len(stripped)


class PageEvidence:
    """Maski tuszu strony (liczone raz) i kontrola elementów ``vlm-*`` tej strony."""

    def __init__(
        self,
        image: ImageArray,
        page: int,
        dpi: float,
        blocks: Sequence[TextBlock],
        cfg: FusionConfig | None = None,
        detect_cfg: DetectConfig | None = None,
    ) -> None:
        self.image = image
        self.page = page
        self.scale = Scale(dpi)
        self.boxes = [(b.bbox, b.text) for b in blocks if b.page == page and b.bbox is not None]
        self.cfg = cfg or FusionConfig()
        self.detect_cfg = detect_cfg or DetectConfig()
        self.height, self.width = int(image.shape[0]), int(image.shape[1])

    @cached_property
    def _masks(self) -> tuple[Mask, Mask]:
        gray: Gray = to_gray(self.image)
        sigma = self.scale.px(self.detect_cfg.blur_sigma_mm)
        if sigma >= 0.3:  # to samo odszumianie co w preprocessingu detektorów
            gray = np.asarray(cv2.GaussianBlur(gray, (0, 0), sigma, sigmaY=sigma), dtype=np.uint8)
        binary = binarize(gray, self.detect_cfg, self.scale)
        return binary.ink, binary.light_ink

    def _crop_box(self, element: VisualElement) -> PixelBox:
        margin = self.scale.px(self.cfg.evidence_margin_mm)
        b = element.bbox
        x0 = max(0, int(np.floor(b.x0 * self.width - margin)))
        y0 = max(0, int(np.floor(b.y0 * self.height - margin)))
        x1 = min(self.width, int(np.ceil(b.x1 * self.width + margin)))
        y1 = min(self.height, int(np.ceil(b.y1 * self.height + margin)))
        return x0, y0, x1, y1

    def _strokes(self, mask: Mask, length: int, horizontal: bool) -> list[int]:
        size = (max(3, length), 1) if horizontal else (1, max(3, length))
        return [
            (b[2] - b[0]) if horizontal else (b[3] - b[1]) for b in components(_open(mask, size))
        ]

    def _evidence(self, element: VisualElement, box: PixelBox) -> tuple[str, str] | None:
        ink, light = self._masks
        x0, y0, x1, y1 = box
        crop, crop_light = ink[y0:y1, x0:x1], light[y0:y1, x0:x1]
        b = element.bbox
        width = max(1.0, (b.x1 - b.x0) * self.width)
        height = max(1.0, (b.y1 - b.y0) * self.height)
        cfg = self.cfg
        kind = element.kind

        if kind in (ElementKind.DOTTED_LINE, ElementKind.SIGNATURE):
            relaxed = self.detect_cfg.model_copy(
                update={
                    "dot_min_count": cfg.evidence_dot_min_count,
                    "dot_min_span_mm": cfg.evidence_dot_min_span_mm,
                }
            )
            chains = dotted_lines(crop_light, relaxed, self.scale)
            span = max((c.box[2] - c.box[0] for c in chains), default=0)
            # Ślad musi obejmować co najmniej połowę ramki, jak odcinek przy linii ciągłej:
            # kilka kropek w wierszu tekstu nie potwierdza linii na całą szerokość strony.
            if span >= cfg.evidence_line_share * width:
                return "DOTS", f"kropki na {span / width:.2f} szerokości"
        if kind in LINE_KINDS or kind is ElementKind.SIGNATURE:
            kernel = min(self.scale.ipx(cfg.evidence_line_min_mm), round(0.5 * width))
            longest = max(self._strokes(crop, kernel, horizontal=True), default=0)
            if longest >= cfg.evidence_line_share * width:
                return "LINE", f"odcinek {longest / width:.2f} szerokości"
        if kind is ElementKind.CHAR_BOXES:
            tall = round(cfg.evidence_stroke_share * height)
            strokes = [s for s in self._strokes(crop, tall, horizontal=False) if s >= tall]
            if len(strokes) >= 2:
                return "STROKES", f"kreski pionowe: {len(strokes)}"
        if kind in BOX_KINDS:
            side = min(width, height)
            tall = round(cfg.evidence_stroke_share * side)
            vertical = [s for s in self._strokes(crop, tall, horizontal=False) if s >= tall]
            horizontal = [s for s in self._strokes(crop, tall, horizontal=True) if s >= tall]
            if len(vertical) >= 2 and len(horizontal) >= 2:
                return "BOX", f"kreski: {len(vertical)} pionowe, {len(horizontal)} poziome"
        return None

    def _text_cover(self, element: VisualElement) -> float:
        """Część szerokości ramki zajęta przez bloki tekstu (nie wypełniacze) w pasie ramki."""
        b = element.bbox
        pad = self.scale.px(self.cfg.evidence_margin_mm) / self.height
        band0, band1 = b.y0 - pad, b.y1 + pad
        spans: list[tuple[float, float]] = []
        for box, text in self.boxes:
            overlap = min(band1, box.y1) - max(band0, box.y0)
            if overlap < 0.5 * min(box.y1 - box.y0, band1 - band0) or is_filler(text):
                continue
            left, right = max(b.x0, box.x0), min(b.x1, box.x1)
            if right > left:
                spans.append((left, right))
        covered, end = 0.0, b.x0
        for left, right in sorted(spans):
            left = max(left, end)
            if right > left:
                covered += right - left
                end = right
        return covered / max(b.x1 - b.x0, 1e-9)

    def _glyphs(self, box: PixelBox) -> int:
        ink, _ = self._masks
        x0, y0, x1, y1 = box
        low = self.scale.px(self.cfg.evidence_glyph_min_mm)
        high = self.scale.px(self.cfg.evidence_glyph_max_mm)
        return sum(
            1
            for c in components(ink[y0:y1, x0:x1])
            if low <= c[3] - c[1] <= high and c[2] - c[0] <= high
        )

    def verify(self, element: VisualElement) -> Verdict:
        if element.page != self.page:
            raise ValueError(f"element {element.id} nie należy do strony {self.page}")
        box = self._crop_box(element)
        if box[2] <= box[0] or box[3] <= box[1]:
            return Verdict(True, codes.NO_VISUAL_EVIDENCE, "BLANK", "ramka poza stroną")
        with ipp_disabled():
            found = self._evidence(element, box)
            if found is not None:
                return Verdict(True, codes.WEAK_VISUAL_EVIDENCE, *found)
            cover = self._text_cover(element)
            if cover >= self.cfg.evidence_text_cover:
                return Verdict(
                    False, codes.VLM_ONLY_REJECTED, "TEXT_ROW", f"tekst {cover:.2f} szerokości"
                )
            glyphs = self._glyphs(box)
            if glyphs >= self.cfg.evidence_glyph_count:
                return Verdict(False, codes.VLM_ONLY_REJECTED, "TEXT_ROW", f"znaki: {glyphs}")
            ink, _ = self._masks
            share = float(ink[box[1] : box[3], box[0] : box[2]].mean())
        if share < self.cfg.evidence_ink_blank:
            return Verdict(True, codes.NO_VISUAL_EVIDENCE, "BLANK", f"tusz {share:.4f}")
        return Verdict(True, codes.NO_VISUAL_EVIDENCE, "INK", f"tusz {share:.4f} bez struktury")


def make_verifier(
    image: ImageArray,
    page: int,
    dpi: float,
    blocks: Sequence[TextBlock],
    cfg: FusionConfig | None = None,
    detect_cfg: DetectConfig | None = None,
) -> Verifier:
    """Funkcja kontroli ``vlm-*`` strony (do ``PageFusionInput.verify``); maski liczone leniwie."""
    return PageEvidence(image, page, dpi, blocks, cfg, detect_cfg).verify
