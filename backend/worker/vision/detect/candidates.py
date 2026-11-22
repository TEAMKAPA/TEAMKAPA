"""Kandydat na element (wynik detektorów przed nadaniem ID i przeliczeniem do układu strony)."""

from dataclasses import dataclass

from backend.worker.vision.detect.preprocess import PixelBox
from backend.worker.vision.models import ElementKind


@dataclass(frozen=True)
class Candidate:
    kind: ElementKind
    box: PixelBox  # krawędzie pikseli obrazu wyprostowanego
    confidence: float
    reasons: tuple[str, ...] = ()
    cells: int | None = None
    members: tuple[PixelBox, ...] = ()  # ramki checkboxów grupy (C.6)
