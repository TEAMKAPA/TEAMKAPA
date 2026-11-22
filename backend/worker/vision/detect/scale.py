"""Przeliczanie milimetrów strony na piksele według efektywnego DPI strony."""

import math
from dataclasses import dataclass

from backend.worker.vision.models import PageMeta

MM_PER_INCH = 25.4
A4_MM = (210.0, 297.0)
A4_TOLERANCE = 0.03  # względna zgodność proporcji strony z A4


def effective_dpi(meta: PageMeta, default_dpi: float) -> float:
    """DPI używane do skalowania progów.

    Kolejno: DPI strony (render PDF lub metadane obrazu), oszacowanie z Etapu A
    (``dpi_estimate``), szerokość strony o proporcjach A4 (pion lub poziom), wartość domyślna.
    """
    if meta.dpi is not None:
        return float(meta.dpi)
    if meta.dpi_estimate is not None:
        return float(meta.dpi_estimate)
    return a4_dpi(meta.width_px, meta.height_px) or default_dpi


def a4_dpi(width_px: int, height_px: int) -> float | None:
    """DPI strony o proporcjach A4 (krótszy bok = 210 mm) albo ``None``."""
    short, long = sorted((width_px, height_px))
    ratio = long / short
    if abs(ratio / (A4_MM[1] / A4_MM[0]) - 1) > A4_TOLERANCE:
        return None
    return short / (A4_MM[0] / MM_PER_INCH)


@dataclass(frozen=True)
class Scale:
    dpi: float

    def px(self, mm: float) -> float:
        return mm * self.dpi / MM_PER_INCH

    def ipx(self, mm: float, minimum: int = 1) -> int:
        """Całkowita liczba pikseli (zaokrąglenie), co najmniej ``minimum``."""
        return max(minimum, round(self.px(mm)))

    def odd(self, mm: float, minimum: int = 3) -> int:
        """Nieparzysty rozmiar okna (np. binaryzacji adaptacyjnej)."""
        value = max(minimum, math.floor(self.px(mm)))
        return value if value % 2 else value + 1

    def mm(self, px: float) -> float:
        return px * MM_PER_INCH / self.dpi
