"""Wczytywanie obrazów JPEG/PNG jako stron, z cięciem zszytych stron A4.

Kolejność: dekodowanie (limit pikseli, tylko oczekiwany format) → pierwsza klatka → orientacja
EXIF → RGB uint8 (przezroczystość na białym tle, 16 bit → 8 bit) → ewentualne cięcie zszytych
stron A4 → limit pikseli strony (``DPI_REDUCED``) → metadane.

Cięcie (decyzja 9 planu Etapu A): obraz pionowy o wysokości k·round(w·√2) ± tolerancja (k ≥ 2),
w którym pas ±margines wokół każdej granicy stron nie zawiera tuszu (piksel ciemniejszy niż próg
w dowolnym kanale). Wysoki obraz, który tych warunków nie spełnia, zostaje jedną stroną
z ostrzeżeniem ``TALL_IMAGE_UNSPLIT``. Wszystkie progi są w ``RenderConfig``.
"""

import io
import math
import threading
import warnings
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

import numpy as np
import PIL
from PIL import Image, ImageOps, UnidentifiedImageError, features

from backend.shared.schemas.quality import Severity
from backend.worker.vision.config import RenderConfig
from backend.worker.vision.errors import IssueCode, RenderError
from backend.worker.vision.models import (
    DocumentKind,
    DpiEstimateSource,
    DpiSource,
    ImageArray,
    PageMeta,
    PageOrigin,
    PageSource,
    RenderedPage,
    RenderIssue,
    Rotation,
)
from backend.worker.vision.render.isolation import IsolationFailure, run_isolated

A4_SHORT_SIDE_IN = 210 / 25.4
_SQRT2 = math.sqrt(2.0)
_EXIF_ORIENTATION = 0x0112
_EXIF_ROTATION: dict[int, Rotation] = {1: 0, 2: 0, 3: 180, 4: 180, 5: 90, 6: 90, 7: 270, 8: 270}
"""Kąt obrotu (zgodnie z ruchem wskazówek) zawarty w orientacji EXIF; 2, 4, 5, 7 mają też lustro."""
_PILLOW_FORMAT = {DocumentKind.JPEG: "JPEG", DocumentKind.PNG: "PNG"}
_DPI_RANGE = (1.0, 2400.0)
_WHITE = (255, 255, 255, 255)
_SIXTEEN_BIT_MODES = frozenset({"I;16", "I;16L", "I;16B", "I;16N"})

_pillow_lock = threading.Lock()


@dataclass(frozen=True, slots=True)
class ImageInfo:
    frames: int
    page_count: int
    renderer_versions: dict[str, str]
    issues: tuple[RenderIssue, ...]


@dataclass(frozen=True, slots=True)
class ImageRenderResult:
    info: ImageInfo
    pages: tuple[RenderedPage, ...]


@dataclass(frozen=True, slots=True)
class _Decoded:
    image: ImageArray
    frames: int
    orientation: int | None
    dpi: float | None
    dpi_problem: str


def renderer_versions() -> dict[str, str]:
    versions = {"pillow": PIL.__version__}
    for feature in ("libjpeg_turbo", "zlib"):
        version = features.version(feature)
        if version:
            versions[feature] = str(version)
    return versions


# --- dekodowanie ------------------------------------------------------------------------------


@contextmanager
def _pillow_limits(max_pixels: int) -> Iterator[None]:
    """Limit pikseli Pillow na czas dekodowania; ostrzeżenie o bombie dekompresyjnej = błąd.

    ``Image.MAX_IMAGE_PIXELS`` jest globalny, więc zmiana jest chroniona blokadą i cofana.
    W trybie izolowanym dekodowanie odbywa się w osobnym procesie.
    """
    with _pillow_lock:
        previous = Image.MAX_IMAGE_PIXELS
        Image.MAX_IMAGE_PIXELS = max_pixels
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                yield
        finally:
            Image.MAX_IMAGE_PIXELS = previous


def _decode(data: bytes, kind: DocumentKind, cfg: RenderConfig) -> _Decoded:
    fmt = _PILLOW_FORMAT.get(kind)
    if fmt is None:
        raise ValueError(f"nie jest obrazem: {kind}")
    with _pillow_limits(cfg.max_image_pixels):
        try:
            with Image.open(io.BytesIO(data), formats=[fmt]) as im:
                width, height = im.size
                if width * height > cfg.max_image_pixels:
                    raise RenderError(IssueCode.IMAGE_TOO_LARGE, f"{width}x{height}px")
                frames = int(getattr(im, "n_frames", 1))
                raw_orientation = im.getexif().get(_EXIF_ORIENTATION)
                orientation = raw_orientation if raw_orientation in _EXIF_ROTATION else None
                dpi, dpi_problem = _metadata_dpi(im.info.get("dpi"))
                im.seek(0)
                im.load()
                upright = ImageOps.exif_transpose(im)
                image = _to_rgb(upright)
        except RenderError:
            raise
        except Image.DecompressionBombError, Image.DecompressionBombWarning:
            raise RenderError(IssueCode.IMAGE_TOO_LARGE, "decompression bomb limit") from None
        except (UnidentifiedImageError, OSError, ValueError, SyntaxError, EOFError) as exc:
            raise RenderError(IssueCode.IMAGE_UNREADABLE, f"pillow {type(exc).__name__}") from None
    return _Decoded(image, frames, orientation, dpi, dpi_problem)


def _metadata_dpi(raw: object) -> tuple[float | None, str]:
    """DPI z metadanych, gdy jest wiarygodne: oba kierunki w zakresie i równe (±1%)."""
    if raw is None:
        return None, "no dpi metadata"
    if not (isinstance(raw, tuple) and len(raw) == 2):
        return None, "invalid dpi metadata"
    try:
        x, y = float(raw[0]), float(raw[1])
    except TypeError, ValueError:
        return None, "invalid dpi metadata"
    lo, hi = _DPI_RANGE
    if not (lo <= x <= hi and lo <= y <= hi) or abs(x - y) > 0.01 * max(x, y):
        return None, "invalid dpi metadata"
    # PNG zapisuje gęstość w całych pikselach na metr (200 DPI -> 199,9996), więc zaokrąglamy
    # do 0,01 DPI, czyli w granicach dokładności formatu.
    return round(x, 2), ""


def _to_rgb(im: Image.Image) -> ImageArray:
    """RGB uint8; przezroczystość nakładana na białe tło, 16 bit skalowane do 8 bit."""
    if im.mode in _SIXTEEN_BIT_MODES or im.mode == "I":
        gray16 = np.clip(np.asarray(im, dtype=np.int64), 0, 65535)
        gray = (gray16 >> 8).astype(np.uint8)
        return np.ascontiguousarray(np.repeat(gray[:, :, None], 3, axis=2))
    if im.mode == "F":
        gray = np.clip(np.asarray(im, dtype=np.float64), 0, 255).round().astype(np.uint8)
        return np.ascontiguousarray(np.repeat(gray[:, :, None], 3, axis=2))
    has_alpha = im.mode in ("RGBA", "LA", "PA", "La", "RGBa") or "transparency" in im.info
    if has_alpha:
        rgba = im.convert("RGBA")
        background = Image.new("RGBA", rgba.size, _WHITE)
        rgb_image = Image.alpha_composite(background, rgba).convert("RGB")
    else:
        rgb_image = im.convert("RGB")
    rgb: ImageArray = np.array(rgb_image, dtype=np.uint8, copy=True, order="C")
    return rgb


# --- cięcie zszytych stron ---------------------------------------------------------------------


def plan_split(image: ImageArray, cfg: RenderConfig) -> tuple[list[int] | None, str]:
    """Granice stron (``[0, b1, ..., h]``) albo ``None`` i powód (bez treści dokumentu)."""
    height, width = image.shape[:2]
    if height <= width:
        return None, "not portrait"
    page_height = round(width * _SQRT2)
    parts = round(height / page_height)
    if parts < 2:
        return None, f"ratio={height / width:.3f}"
    if abs(height - parts * page_height) > cfg.split_height_tolerance_px:
        return None, f"height={height} not {parts}x{page_height}"
    bounds = [round(i * height / parts) for i in range(parts + 1)]
    darkest = image.min(axis=2)
    margin = cfg.split_seam_margin_px
    for seam in bounds[1:-1]:
        band = darkest[max(0, seam - margin) : min(height, seam + margin)]
        if bool((band < cfg.split_ink_threshold).any()):
            return None, f"ink at seam y={seam}"
    return bounds, f"parts={parts}"


def _is_a4_ratio(width: int, height: int, cfg: RenderConfig) -> bool:
    ratio = max(width, height) / min(width, height)
    return abs(ratio / _SQRT2 - 1.0) <= cfg.a4_ratio_tolerance


def _fit_pixels(
    part: ImageArray, dpi: float | None, cfg: RenderConfig
) -> tuple[ImageArray, float | None, RenderIssue | None]:
    """Zmniejsza stronę ponad ``max_page_pixels`` (LANCZOS, deterministycznie): DPI_REDUCED."""
    height, width = part.shape[:2]
    if width * height <= cfg.max_page_pixels:
        return np.array(part, dtype=np.uint8, copy=True, order="C"), dpi, None
    scale = math.sqrt(cfg.max_page_pixels / (width * height))
    new_w, new_h = max(1, math.floor(width * scale)), max(1, math.floor(height * scale))
    resized = Image.fromarray(part).resize((new_w, new_h), Image.Resampling.LANCZOS)
    out: ImageArray = np.array(resized, dtype=np.uint8, copy=True, order="C")
    actual = new_w / width
    new_dpi = dpi * actual if dpi is not None else None
    detail = f"dpi {dpi:g}->{new_dpi:g}" if dpi is not None and new_dpi else f"scale {actual:.4f}"
    issue = RenderIssue(code=IssueCode.DPI_REDUCED, severity=Severity.WARNING, detail=detail)
    return out, new_dpi, issue


# --- strony ------------------------------------------------------------------------------------


def iter_image_pages(
    data: bytes, kind: DocumentKind, cfg: RenderConfig
) -> Iterator[ImageInfo | tuple[PageMeta, ImageArray]]:
    """Najpierw ``ImageInfo``, potem kolejno ``(PageMeta, obraz RGB)``."""
    decoded = _decode(data, kind, cfg)
    image = decoded.image
    height, width = image.shape[:2]

    doc_issues: list[RenderIssue] = []
    if decoded.frames > 1:
        doc_issues.append(
            RenderIssue(
                code=IssueCode.MULTI_FRAME_IMAGE,
                severity=Severity.WARNING,
                detail=f"frames={decoded.frames} used=1",
            )
        )

    bounds, reason = plan_split(image, cfg)
    split = bounds is not None
    if bounds is None:
        bounds = [0, height]
    parts = len(bounds) - 1
    if parts > cfg.max_pages:
        raise RenderError(IssueCode.TOO_MANY_PAGES, f"pages={parts} limit={cfg.max_pages}")
    tall_unsplit = not split and height / width > cfg.tall_image_ratio

    yield ImageInfo(
        frames=decoded.frames,
        page_count=parts,
        renderer_versions=renderer_versions(),
        issues=tuple(doc_issues),
    )

    for index in range(parts):
        top, bottom = bounds[index], bounds[index + 1]
        issues: list[RenderIssue] = []
        if split:
            issues.append(
                RenderIssue(
                    code=IssueCode.IMAGE_SPLIT_A4,
                    severity=Severity.INFO,
                    detail=f"part={index + 1}/{parts}",
                )
            )
        if tall_unsplit:
            issues.append(
                RenderIssue(
                    code=IssueCode.TALL_IMAGE_UNSPLIT, severity=Severity.WARNING, detail=reason
                )
            )
        page_image, dpi, reduced = _fit_pixels(image[top:bottom], decoded.dpi, cfg)
        if reduced is not None:
            issues.append(reduced)
        page_h, page_w = page_image.shape[:2]

        estimate: float | None = None
        if dpi is None and (split or _is_a4_ratio(page_w, page_h, cfg)):
            estimate = min(page_w, page_h) / A4_SHORT_SIDE_IN
        if dpi is None:
            issues.append(
                RenderIssue(
                    code=IssueCode.DPI_UNKNOWN,
                    severity=Severity.INFO if estimate is not None else Severity.WARNING,
                    detail=decoded.dpi_problem,
                )
            )

        origin = (
            PageOrigin(
                image_width_px=width,
                image_height_px=height,
                offset_y_px=top,
                part_height_px=bottom - top,
                part=index + 1,
                parts=parts,
            )
            if split
            else None
        )
        meta = PageMeta(
            page=index + 1,
            width_px=page_w,
            height_px=page_h,
            dpi=dpi,
            dpi_source=DpiSource.METADATA if dpi is not None else DpiSource.UNKNOWN,
            dpi_estimate=estimate,
            dpi_estimate_source=DpiEstimateSource.A4_WIDTH if estimate is not None else None,
            rotation=_EXIF_ROTATION.get(decoded.orientation or 1, 0),
            exif_orientation=decoded.orientation,
            source=PageSource.IMAGE,
            origin=origin,
            issues=tuple(issues),
        )
        yield meta, page_image


def render_image(
    data: bytes, kind: DocumentKind, cfg: RenderConfig, *, isolated: bool = True
) -> ImageRenderResult:
    """Wczytuje obraz jako strony. Domyślnie w procesie potomnym z limitem czasu na stronę."""
    if not isolated:
        return _collect(iter_image_pages(data, kind, cfg))
    items: list[ImageInfo | tuple[PageMeta, ImageArray]] = []
    try:
        for item in run_isolated(
            _isolated_target,
            (data, kind.value, cfg.model_dump()),
            item_timeout_s=cfg.page_timeout_s,
            startup_timeout_s=cfg.worker_startup_timeout_s,
        ):
            items.append(item)
    except IsolationFailure as exc:
        where = "decode" if exc.step <= 1 else f"page={exc.step - 1}"
        raise RenderError(exc.code, f"{where} {exc.detail}") from None
    return _collect(iter(items))


def _isolated_target(payload: tuple[bytes, str, dict[str, object]]) -> Iterator[object]:
    data, kind, cfg_dump = payload
    yield from iter_image_pages(data, DocumentKind(kind), RenderConfig.model_validate(cfg_dump))


def _collect(items: Iterator[ImageInfo | tuple[PageMeta, ImageArray]]) -> ImageRenderResult:
    first = next(items, None)
    if not isinstance(first, ImageInfo):
        raise RenderError(IssueCode.IMAGE_UNREADABLE, "no image info")
    pages: list[RenderedPage] = []
    for item in items:
        if not isinstance(item, tuple):
            raise RenderError(IssueCode.IMAGE_UNREADABLE, "unexpected item")
        meta, image = item
        pages.append(RenderedPage(meta=meta, image=image))
    if len(pages) != first.page_count:
        raise RenderError(
            IssueCode.IMAGE_UNREADABLE, f"pages={len(pages)} expected={first.page_count}"
        )
    return ImageRenderResult(info=first, pages=tuple(pages))
