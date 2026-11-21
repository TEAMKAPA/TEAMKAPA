"""Wspólny układ współrzędnych kanału wizualnego.

Układ wyjściowy (``BoundingBox`` z #17): ``x0, y0, x1, y1`` w ułamkach szerokości i wysokości
strony (0..1), początek w lewym górnym rogu, strona tak, jak jest wyświetlana (po ``/Rotate``
i orientacji EXIF).

Konwencja pikseli: prostokąt w pikselach opisuje **krawędzie** pikseli. Piksel o indeksie
kolumny ``j`` zajmuje przedział ``[j, j + 1)``, więc prostokąt obejmujący kolumny 10..19
to ``x0 = 10, x1 = 20``.
Dzięki temu ułamek nie zależy od rozdzielczości: ``x / szerokość``.

Funkcje ``*_to_norm`` zwracają surową krotkę (``NormTuple``), która może wyjść poza stronę.
``BoundingBox`` powstaje dopiero przez ``to_bbox`` (ściśle: błąd poza stroną) albo ``clip_to_page``
(przycięcie z jawną flagą). Przycięcie nigdy nie odbywa się po cichu.
"""

import math

from backend.shared.schemas.quality import BoundingBox
from backend.worker.vision.errors import CoordinateError
from backend.worker.vision.models import PageGeometry, PageOrigin, PdfBox, Rotation

NormTuple = tuple[float, float, float, float]
PixelBox = tuple[float, float, float, float]

EPS = 1e-9
"""Tolerancja błędów zmiennoprzecinkowych: wartości w [-EPS, 1 + EPS] są przycinane bez flagi."""

_ROTATIONS: frozenset[int] = frozenset({0, 90, 180, 270})


def _ordered(raw: NormTuple) -> NormTuple:
    x0, y0, x1, y1 = raw
    return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))


def _check_finite(raw: tuple[float, ...]) -> None:
    if not all(math.isfinite(v) for v in raw):
        raise CoordinateError("współrzędne muszą być skończone")


def _clamp01(v: float) -> float:
    return 0.0 if v < 0.0 else 1.0 if v > 1.0 else v


def to_bbox(raw: NormTuple) -> BoundingBox:
    """Ścisła konwersja: każda współrzędna poza [-EPS, 1 + EPS] to ``CoordinateError``."""
    _check_finite(raw)
    x0, y0, x1, y1 = _ordered(raw)
    if min(x0, y0) < -EPS or max(x1, y1) > 1.0 + EPS:
        raise CoordinateError("bbox poza stroną")
    return BoundingBox(x0=_clamp01(x0), y0=_clamp01(y0), x1=_clamp01(x1), y1=_clamp01(y1))


def clip_to_page(raw: NormTuple) -> tuple[BoundingBox, bool]:
    """Przycina do strony. Zwraca ``(bbox, przycięto)``; ``przycięto`` dotyczy tylko zmian > EPS.

    Prostokąt w całości poza stroną to ``CoordinateError`` (nie ma czego przycinać).
    """
    _check_finite(raw)
    x0, y0, x1, y1 = _ordered(raw)
    if x1 < -EPS or y1 < -EPS or x0 > 1.0 + EPS or y0 > 1.0 + EPS:
        raise CoordinateError("bbox w całości poza stroną")
    clipped = min(x0, y0) < -EPS or max(x1, y1) > 1.0 + EPS
    box = BoundingBox(x0=_clamp01(x0), y0=_clamp01(y0), x1=_clamp01(x1), y1=_clamp01(y1))
    return box, clipped


def bbox_as_list(bbox: BoundingBox) -> list[float]:
    """Postać listy ``[x0, y0, x1, y1]`` (format z briefu kroku 3)."""
    return [bbox.x0, bbox.y0, bbox.x1, bbox.y1]


def _check_size(width_px: float, height_px: float) -> None:
    if not (width_px > 0 and height_px > 0 and math.isfinite(width_px * height_px)):
        raise CoordinateError("rozmiar strony musi być dodatni")


def px_to_norm(box_px: PixelBox, width_px: float, height_px: float) -> NormTuple:
    """Krawędzie pikseli → ułamki strony."""
    _check_size(width_px, height_px)
    _check_finite(box_px)
    x0, y0, x1, y1 = box_px
    return _ordered((x0 / width_px, y0 / height_px, x1 / width_px, y1 / height_px))


def norm_to_px(bbox: BoundingBox, width_px: float, height_px: float) -> PixelBox:
    """Ułamki strony → krawędzie pikseli (wartości zmiennoprzecinkowe)."""
    _check_size(width_px, height_px)
    return (bbox.x0 * width_px, bbox.y0 * height_px, bbox.x1 * width_px, bbox.y1 * height_px)


def norm_to_px_int(bbox: BoundingBox, width_px: int, height_px: int) -> tuple[int, int, int, int]:
    """Najmniejszy prostokąt całych pikseli obejmujący bbox (do wycinania fragmentu obrazu)."""
    x0, y0, x1, y1 = norm_to_px(bbox, width_px, height_px)
    tol = 1e-6
    return (
        max(0, math.floor(x0 + tol)),
        max(0, math.floor(y0 + tol)),
        min(width_px, math.ceil(x1 - tol)),
        min(height_px, math.ceil(y1 - tol)),
    )


def check_rotation(rotation: int) -> Rotation:
    """Normalizuje ``/Rotate`` (wielokrotność 90, także ujemna lub ≥ 360) do 0/90/180/270."""
    if rotation % 90 != 0:
        raise CoordinateError("obrót musi być wielokrotnością 90 stopni")
    r = rotation % 360
    if r == 0:
        return 0
    if r == 90:
        return 90
    if r == 180:
        return 180
    return 270


def rotate_norm(raw: NormTuple, rotation: int) -> NormTuple:
    """Obraca prostokąt razem ze stroną o ``rotation`` stopni **zgodnie z ruchem wskazówek zegara**.

    Odpowiada ``/Rotate`` w PDF: punkt (u, v) strony nieobróconej przechodzi w
    90°: (1 - v, u), 180°: (1 - u, 1 - v), 270°: (v, 1 - u).
    """
    _check_finite(raw)
    r = check_rotation(rotation)
    x0, y0, x1, y1 = raw
    if r == 0:
        out = (x0, y0, x1, y1)
    elif r == 90:
        out = (1.0 - y0, x0, 1.0 - y1, x1)
    elif r == 180:
        out = (1.0 - x0, 1.0 - y0, 1.0 - x1, 1.0 - y1)
    else:
        out = (y0, 1.0 - x0, y1, 1.0 - x1)
    return _ordered(out)


def pdf_rect_to_norm(rect_pt: PdfBox, geometry: PageGeometry) -> NormTuple:
    """Prostokąt w przestrzeni użytkownika PDF (punkty, lewy dolny róg) → ułamki strony.

    Uwzględnia przesunięcie i rozmiar widocznego obszaru (CropBox ∩ MediaBox) oraz ``/Rotate``.
    Zakłada brak dodatkowej macierzy przekształcenia (bbox z PDFium, Doclinga, AcroForm).
    """
    _check_finite(rect_pt)
    vx0, vy0, vx1, vy1 = geometry.visible_box
    w, h = vx1 - vx0, vy1 - vy0
    x0, y0, x1, y1 = rect_pt
    # Układ nieobrócony z początkiem w lewym górnym rogu: oś y w dół.
    unrotated = _ordered(((x0 - vx0) / w, (vy1 - y0) / h, (x1 - vx0) / w, (vy1 - y1) / h))
    return rotate_norm(unrotated, geometry.rotation)


def part_to_original(raw: NormTuple, origin: PageOrigin, part_height_px: int) -> NormTuple:
    """Bbox strony wyciętej ze zszytego obrazu → ułamki oryginalnego obrazu."""
    _check_finite(raw)
    if part_height_px < 1 or origin.offset_y_px + part_height_px > origin.image_height_px:
        raise CoordinateError("wysokość części niezgodna z oryginałem")
    x0, y0, x1, y1 = raw
    h = origin.image_height_px

    def oy(v: float) -> float:
        return (origin.offset_y_px + v * part_height_px) / h

    return _ordered((x0, oy(y0), x1, oy(y1)))
