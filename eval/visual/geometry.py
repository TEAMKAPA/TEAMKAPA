"""Tożsamość obrazów stron i zgodność geometrii wariantów (#12).

Materiały COI zawierają ten sam formularz w kilku formatach (PDF, PNG, JPG, JPEG). Strona obrazu
korzysta z etykiet strony PDF (jest jej *wariantem*), gdy oba obrazy mają identyczny rozmiar,
przesunięcie treści wynosi co najwyżej ``max_shift_px``, a tusz każdego z obrazów leży w odległości
co najwyżej ``tolerance_px`` od tuszu drugiego (po wyrównaniu przesunięcia) w odsetku
co najmniej ``min_ink_fraction``. Wartości domyślne wynikają z pomiaru A.8: przesunięcie 0–1 px,
pokrycie tuszu z tolerancją 1 px co najmniej 0,979.

Etykiety wariantu nie są przesuwane: błąd położenia to najwyżej ``max_shift_px`` (1 px przy 200 DPI,
około 0,0004 wysokości strony A4).
"""

import hashlib
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from backend.worker.vision.models import ImageArray
from eval.visual.models import GeometryCheck

Mask = npt.NDArray[np.bool_]


@dataclass(frozen=True, slots=True)
class GeometryParams:
    max_shift_px: int = 1
    tolerance_px: int = 1
    min_ink_fraction: float = 0.97
    ink_threshold: int = (
        200  # piksel ciemniejszy w dowolnym kanale to tusz (jak przy cięciu obrazów)
    )
    search_px: int = (
        3  # zakres szukania przesunięcia (większe przesunięcie i tak oznacza odrzucenie)
    )


def image_sha256(image: ImageArray) -> str:
    """Hash pikseli obrazu strony (z kształtem i typem), niezależny od formatu zapisu pliku."""
    array = np.ascontiguousarray(image)
    header = f"{array.dtype.str}:{'x'.join(str(n) for n in array.shape)}:".encode("ascii")
    return hashlib.sha256(header + array.tobytes()).hexdigest()


def ink_mask(image: ImageArray, threshold: int) -> Mask:
    mask: Mask = image.min(axis=2) < threshold
    return mask


def shift_mask(mask: Mask, dx: int, dy: int) -> Mask:
    """Przesuwa maskę o ``(dx, dy)`` pikseli (w prawo i w dół) bez zawijania krawędzi."""
    h, w = mask.shape
    out = np.zeros_like(mask)
    if abs(dx) >= w or abs(dy) >= h:
        return out
    out[max(dy, 0) : h + min(dy, 0), max(dx, 0) : w + min(dx, 0)] = mask[
        max(-dy, 0) : h - max(dy, 0), max(-dx, 0) : w - max(dx, 0)
    ]
    return out


def dilate(mask: Mask, radius: int) -> Mask:
    """Dylatacja kwadratowym sąsiedztwem (odległość Czebyszewa ``radius``)."""
    out = mask.copy()
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            if dx or dy:
                out |= shift_mask(mask, dx, dy)
    return out


def _overlap(base: Mask, variant: Mask, dx: int, dy: int) -> int:
    """Wspólny tusz bazy i wariantu cofniętego o ``(dx, dy)`` (widoki tablic, bez kopiowania)."""
    h, w = base.shape
    b = base[max(-dy, 0) : h - max(dy, 0), max(-dx, 0) : w - max(dx, 0)]
    v = variant[max(dy, 0) : h + min(dy, 0), max(dx, 0) : w + min(dx, 0)]
    return int(np.count_nonzero(b & v))


def best_shift(base: Mask, variant: Mask, search_px: int) -> tuple[int, int]:
    """Przesunięcie treści wariantu względem bazy (dodatnie: w prawo i w dół).

    Wybierane jest przesunięcie, po którego cofnięciu wspólny tusz jest największy; remis rozstrzyga
    najmniejsze przesunięcie.
    """
    candidates = (
        (-_overlap(base, variant, dx, dy), abs(dx) + abs(dy), dy, dx)
        for dy in range(-search_px, search_px + 1)
        for dx in range(-search_px, search_px + 1)
    )
    _, _, dy, dx = min(candidates)
    return dx, dy


def near_fraction(source: Mask, reference_dilated: Mask) -> float:
    """Odsetek tuszu ``source`` leżącego w (rozszerzonym) tuszu odniesienia; pusty tusz to 1,0."""
    total = int(np.count_nonzero(source))
    if total == 0:
        return 1.0
    return int(np.count_nonzero(source & reference_dilated)) / total


def compare_pages(
    base: ImageArray, variant: ImageArray, params: GeometryParams | None = None
) -> GeometryCheck:
    """Sprawdza, czy ``variant`` może korzystać z etykiet narysowanych na ``base``."""
    p = params if params is not None else GeometryParams()
    if base.shape != variant.shape:
        return GeometryCheck(
            size_equal=False,
            shift_px=(0, 0),
            variant_ink_near_base=0.0,
            base_ink_near_variant=0.0,
            passed=False,
        )
    base_ink = ink_mask(base, p.ink_threshold)
    variant_ink = ink_mask(variant, p.ink_threshold)
    dx, dy = best_shift(base_ink, variant_ink, p.search_px)
    aligned = shift_mask(variant_ink, -dx, -dy)
    variant_near = round(near_fraction(aligned, dilate(base_ink, p.tolerance_px)), 4)
    base_near = round(near_fraction(base_ink, dilate(aligned, p.tolerance_px)), 4)
    passed = (
        abs(dx) <= p.max_shift_px
        and abs(dy) <= p.max_shift_px
        and variant_near >= p.min_ink_fraction
        and base_near >= p.min_ink_fraction
    )
    return GeometryCheck(
        size_equal=True,
        shift_px=(dx, dy),
        variant_ink_near_base=variant_near,
        base_ink_near_variant=base_near,
        passed=passed,
    )
