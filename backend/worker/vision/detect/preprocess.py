"""Preprocessing strony dla detektorów (C.2): skala szarości, odszumianie, binaryzacja, prostowanie.

- Skala szarości: minimum kanałów RGB, żeby kolorowe linie (np. błękitne obramowania) były ciemne.
- Odszumianie: rozmycie Gaussa o promieniu ``blur_sigma_mm`` (szum skanu przed progowaniem).
- Binaryzacja: piksel jest tuszem, gdy jest ciemniejszy od progu globalnego (Otsu, w granicach
  ``otsu_min``–``otsu_max`` i nie wyżej niż poziom papieru minus ``ink_min_delta``) **i** od
  średniej lokalnej minus ``adaptive_offset`` (cienie skanu).
- Prostowanie: kąt z profilu rzutu wierszy (maksimum sumy kwadratów histogramu) w zakresie
  ±``deskew_max_deg``, najpierw zgrubnie, potem dokładnie; obrót wokół środka strony, gdy
  ``|kąt| ≥ deskew_min_deg``. Ramki wykryte na obrazie wyprostowanym ``Prepared.to_page``
  przelicza z powrotem do układu strony wyrenderowanej (prostokąt obejmujący obrócone narożniki),
  bo w tym układzie są etykiety i kontrakt 7.5.

Współrzędne ramek: krawędzie pikseli (x1, y1 wyłączne). Obrót: kąt dodatni to obrót treści
w lewo (przeciwnie do ruchu wskazówek zegara) na ekranie.
"""

import math
from dataclasses import dataclass

import cv2
import numpy as np
import numpy.typing as npt

from backend.worker.vision.config import DetectConfig
from backend.worker.vision.detect.scale import Scale
from backend.worker.vision.models import ImageArray

Gray = npt.NDArray[np.uint8]
Mask = npt.NDArray[np.bool_]
PixelBox = tuple[int, int, int, int]

SKEW_CORRECTED = "SKEW_CORRECTED"
LOW_CONTRAST = "LOW_CONTRAST"


def rotate_points(
    points: npt.NDArray[np.float64], angle: float, width: int, height: int
) -> npt.NDArray[np.float64]:
    """Obrót punktów (współrzędne krawędzi pikseli) wokół środka strony; kąt dodatni: w lewo."""
    theta = math.radians(angle)
    cx, cy = width / 2, height / 2
    cos, sin = math.cos(theta), math.sin(theta)
    x, y = points[:, 0] - cx, points[:, 1] - cy
    return np.stack([cx + cos * x + sin * y, cy - sin * x + cos * y], axis=1)


@dataclass(frozen=True)
class Prepared:
    gray: Gray  # szarość (po odszumieniu) obrazu wyprostowanego
    ink: Mask  # tusz obrazu wyprostowanego
    light_ink: Mask  # czulsza maska tuszu (tylko do kropek, C.5)
    angle: float  # wykryte pochylenie treści w stopniach (0: bez obrotu)
    threshold: int
    contrast: int  # różnica poziomu papieru i tuszu
    reasons: tuple[str, ...]

    @property
    def width(self) -> int:
        return int(self.ink.shape[1])

    @property
    def height(self) -> int:
        return int(self.ink.shape[0])

    def to_page(self, box: PixelBox) -> PixelBox:
        """Ramka z obrazu wyprostowanego w układzie strony wyrenderowanej."""
        if self.angle == 0.0:
            return box
        x0, y0, x1, y1 = box
        corners = np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], dtype=np.float64)
        moved = rotate_points(corners, self.angle, self.width, self.height)
        return (
            max(0, math.floor(moved[:, 0].min() + 0.5)),
            max(0, math.floor(moved[:, 1].min() + 0.5)),
            min(self.width, math.floor(moved[:, 0].max() + 0.5)),
            min(self.height, math.floor(moved[:, 1].max() + 0.5)),
        )


def to_gray(image: ImageArray) -> Gray:
    gray: Gray = np.ascontiguousarray(image.min(axis=2))
    return gray


def _blur(gray: Gray, cfg: DetectConfig, scale: Scale) -> Gray:
    sigma = scale.px(cfg.blur_sigma_mm)
    if sigma < 0.3:
        return gray
    blurred = cv2.GaussianBlur(gray, (0, 0), sigmaX=sigma, sigmaY=sigma)
    return np.asarray(blurred, dtype=np.uint8)


def paper_noise(gray: Gray, threshold: int) -> float:
    """Odporne odchylenie jasności tła (1,4826 × MAD pikseli jaśniejszych od progu)."""
    paper = gray[gray >= threshold]
    if paper.size == 0:
        return 0.0
    median = float(np.median(paper))
    return 1.4826 * float(np.median(np.abs(paper.astype(np.float32) - median)))


def _light_delta(gray: Gray, threshold: int, cfg: DetectConfig) -> float:
    """Delta czulszej maski: co najmniej ``light_ink_delta`` i ``light_noise_k`` × szum tła."""
    return max(float(cfg.light_ink_delta), cfg.light_noise_k * paper_noise(gray, threshold))


@dataclass(frozen=True)
class Binarized:
    ink: Mask
    light_ink: Mask  # czulsza maska (drobne, jasne ślady: kropki przy niskim DPI)
    threshold: int
    contrast: int


def binarize(gray: Gray, cfg: DetectConfig, scale: Scale) -> Binarized:
    """Maski tuszu, użyty próg globalny i oszacowany kontrast (papier – tusz)."""
    otsu, _ = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    paper = int(np.percentile(gray, 90))
    # Górna granica względem papieru: wyblakły druk ma próg Otsu blisko bieli, a szum na białym
    # tle odcina dodatkowo warunek średniej lokalnej.
    upper = min(cfg.otsu_max, paper - cfg.ink_min_delta)
    threshold = int(min(max(round(otsu), cfg.otsu_min), max(upper, cfg.otsu_min)))
    block = scale.odd(cfg.adaptive_block_mm)
    local = cv2.boxFilter(
        gray.astype(np.float32), -1, (block, block), borderType=cv2.BORDER_REPLICATE
    )
    below_local = gray.astype(np.float32) < local - cfg.adaptive_offset
    ink: Mask = (gray < threshold) & below_local
    light: Mask = (gray < max(threshold, paper - _light_delta(gray, threshold, cfg))) & below_local
    dark = gray[ink]
    contrast = paper - int(np.median(dark)) if dark.size else 255
    return Binarized(ink=ink, light_ink=light, threshold=threshold, contrast=contrast)


def _angles(center: float, half: float, step: float) -> npt.NDArray[np.float64]:
    count = round(half / step)
    values = center + step * np.arange(-count, count + 1, dtype=np.float64)
    # Przy remisie wygrywa kąt bliższy zeru (argmax bierze pierwszy).
    return values[np.lexsort((values, np.abs(values)))]


def estimate_skew(ink: Mask, cfg: DetectConfig, scale: Scale) -> float:
    """Pochylenie treści w stopniach (dodatnie: w lewo); 0, gdy poniżej ``deskew_min_deg``."""
    if cfg.deskew_max_deg == 0 or not ink.any():
        return 0.0
    factor = min(1.0, cfg.deskew_dpi / scale.dpi)
    height, width = ink.shape
    small_size = (max(1, round(width * factor)), max(1, round(height * factor)))
    small = cv2.resize(ink.astype(np.uint8) * 255, small_size, interpolation=cv2.INTER_AREA) > 64
    ys, xs = np.nonzero(small)
    xs_c = xs.astype(np.float64) - small_size[0] / 2
    ys_c = ys.astype(np.float64) - small_size[1] / 2
    bins = small_size[1] * 2

    def score(angle: float) -> float:
        theta = math.radians(angle)
        rows = xs_c * math.sin(theta) + ys_c * math.cos(theta)
        hist, _ = np.histogram(rows, bins=bins, range=(-small_size[1], small_size[1]))
        return float(np.dot(hist.astype(np.float64), hist))

    coarse = _angles(0.0, cfg.deskew_max_deg, cfg.deskew_coarse_step_deg)
    best = float(coarse[int(np.argmax([score(a) for a in coarse]))])
    fine = _angles(best, cfg.deskew_coarse_step_deg, cfg.deskew_fine_step_deg)
    best = float(fine[int(np.argmax([score(a) for a in fine]))])
    best = round(best, 4)
    return best if abs(best) >= cfg.deskew_min_deg else 0.0


def _rotate_gray(gray: Gray, angle: float) -> Gray:
    """Obrót obrazu o ``angle`` stopni w lewo wokół środka strony (białe tło)."""
    height, width = gray.shape
    center = (width / 2 - 0.5, height / 2 - 0.5)  # OpenCV: współrzędne środków pikseli
    matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
    rotated = cv2.warpAffine(
        gray,
        matrix,
        (width, height),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=255,
    )
    return np.asarray(rotated, dtype=np.uint8)


def prepare(image: ImageArray, cfg: DetectConfig, scale: Scale) -> Prepared:
    raw = to_gray(image)
    gray = _blur(raw, cfg, scale)
    masks = binarize(gray, cfg, scale)
    angle = estimate_skew(masks.ink, cfg, scale)
    reasons: list[str] = []
    if angle != 0.0:
        gray = _blur(_rotate_gray(raw, -angle), cfg, scale)
        masks = binarize(gray, cfg, scale)
        reasons.append(SKEW_CORRECTED)
    if masks.contrast < cfg.low_contrast:
        reasons.append(LOW_CONTRAST)
    return Prepared(
        gray=gray,
        ink=masks.ink,
        light_ink=masks.light_ink,
        angle=angle,
        threshold=masks.threshold,
        contrast=masks.contrast,
        reasons=tuple(reasons),
    )
