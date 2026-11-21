"""Renderowanie stron PDF przez pypdfium2 (PDFium, BSD-3-Clause).

Zachowanie PDFium, na którym opiera się układ współrzędnych (sprawdzone testami spójności):
- renderowany jest widoczny obszar CropBox ∩ MediaBox,
- ``/Rotate`` działa zgodnie z ruchem wskazówek zegara; bitmapa ma rozmiar strony po obrocie.

PDFium nie jest bezpieczny wątkowo, a renderowania w C nie da się przerwać, dlatego w pipeline'ie
ta funkcja działa w procesie potomnym (``render.isolation``) z limitem czasu na stronę.
"""

import math
from collections.abc import Iterator
from dataclasses import dataclass

import numpy as np
import pypdfium2 as pdfium  # type: ignore[import-untyped]

from backend.shared.schemas.quality import Severity
from backend.worker.vision.config import RenderConfig
from backend.worker.vision.coords import check_rotation
from backend.worker.vision.errors import CoordinateError, IssueCode, RenderError
from backend.worker.vision.models import (
    DpiSource,
    ImageArray,
    PageGeometry,
    PageMeta,
    PageSource,
    RenderedPage,
    RenderIssue,
)
from backend.worker.vision.render.isolation import IsolationFailure, run_isolated

_PDFIUM_ERR_PASSWORD = 4  # FPDF_ERR_PASSWORD
MIN_DPI = 36
"""Poniżej tego DPI strona jest nieczytelna: zamiast obniżać dalej, zgłaszamy IMAGE_TOO_LARGE."""

_WHITE = (255, 255, 255, 255)


@dataclass(frozen=True, slots=True)
class PdfInfo:
    page_count: int
    renderer_versions: dict[str, str]


def renderer_versions() -> dict[str, str]:
    return {"pypdfium2": str(pdfium.PYPDFIUM_INFO), "pdfium": str(pdfium.PDFIUM_INFO)}


def effective_dpi(
    width_pt: float, height_pt: float, cfg: RenderConfig
) -> tuple[int, RenderIssue | None]:
    """DPI strony: ``cfg.dpi`` albo obniżone tak, by strona zmieściła się w ``max_page_pixels``."""
    pixels = (width_pt * cfg.dpi / 72.0) * (height_pt * cfg.dpi / 72.0)
    if pixels <= cfg.max_page_pixels:
        return cfg.dpi, None
    dpi = math.floor(cfg.dpi * math.sqrt(cfg.max_page_pixels / pixels))
    if dpi < MIN_DPI:
        raise RenderError(
            IssueCode.IMAGE_TOO_LARGE,
            f"page {width_pt:.0f}x{height_pt:.0f}pt needs dpi<{MIN_DPI} to fit limit",
        )
    issue = RenderIssue(
        code=IssueCode.DPI_REDUCED, severity=Severity.WARNING, detail=f"dpi {cfg.dpi}->{dpi}"
    )
    return dpi, issue


def iter_pdf_pages(
    data: bytes, cfg: RenderConfig, source: PageSource = PageSource.NATIVE_PDF
) -> Iterator[PdfInfo | tuple[PageMeta, ImageArray]]:
    """Najpierw zwraca ``PdfInfo``, potem kolejno ``(PageMeta, obraz RGB)`` dla każdej strony.

    Błędy dokumentu zgłasza jako ``RenderError`` z kodem (bez treści dokumentu w szczegółach).
    """
    if source is PageSource.IMAGE:
        raise ValueError("PDF nie może mieć źródła 'image'")
    try:
        pdf = pdfium.PdfDocument(data)
    except pdfium.PdfiumError as exc:
        code = (
            IssueCode.PDF_ENCRYPTED
            if exc.err_code == _PDFIUM_ERR_PASSWORD
            else IssueCode.PDF_UNREADABLE
        )
        raise RenderError(code, f"pdfium err_code={exc.err_code}") from None
    try:
        count = len(pdf)
        if count < 1:
            raise RenderError(IssueCode.PDF_UNREADABLE, "no pages")
        if count > cfg.max_pages:
            raise RenderError(IssueCode.TOO_MANY_PAGES, f"pages={count} limit={cfg.max_pages}")
        if cfg.render_forms:
            pdf.init_forms()  # przed wczytaniem stron: rysowanie wyglądu pól AcroForm
        yield PdfInfo(page_count=count, renderer_versions=renderer_versions())
        for index in range(count):
            yield _render_page(pdf, index, cfg, source)
    finally:
        pdf.close()


def _render_page(
    pdf: pdfium.PdfDocument, index: int, cfg: RenderConfig, source: PageSource
) -> tuple[PageMeta, ImageArray]:
    page = pdf[index]
    try:
        try:
            geometry = PageGeometry(
                mediabox=tuple(page.get_mediabox()),
                cropbox=tuple(page.get_cropbox()),
                rotation=check_rotation(page.get_rotation()),
            )
        except (CoordinateError, ValueError) as exc:
            raise RenderError(IssueCode.PDF_UNREADABLE, f"page={index + 1} geometry") from exc
        width_pt, height_pt = geometry.displayed_size_pt
        dpi, issue = effective_dpi(width_pt, height_pt, cfg)
        bitmap = page.render(
            scale=dpi / 72.0,
            may_draw_forms=cfg.render_forms,
            rev_byteorder=True,
            fill_color=_WHITE,
        )
        try:
            image = _to_rgb(bitmap.to_numpy())
        finally:
            bitmap.close()
        height_px, width_px = image.shape[:2]
        meta = PageMeta(
            page=index + 1,
            width_px=width_px,
            height_px=height_px,
            dpi=float(dpi),
            dpi_source=DpiSource.RENDER,
            rotation=geometry.rotation,
            source=source,
            geometry=geometry,
            issues=(issue,) if issue else (),
        )
        return meta, image
    finally:
        page.close()


def _to_rgb(array: np.ndarray) -> ImageArray:
    """Kopia w pamięci ciągłej (bitmapa PDFium zostaje zwolniona), zawsze RGB uint8."""
    if array.ndim == 2:  # skala szarości
        array = np.repeat(array[:, :, None], 3, axis=2)
    elif array.shape[2] == 4:  # RGBx / RGBA
        array = array[:, :, :3]
    rgb: ImageArray = np.array(array, dtype=np.uint8, copy=True, order="C")
    return rgb


@dataclass(frozen=True, slots=True)
class PdfRenderResult:
    info: PdfInfo
    pages: tuple[RenderedPage, ...]


def render_pdf(
    data: bytes,
    cfg: RenderConfig,
    source: PageSource = PageSource.NATIVE_PDF,
    *,
    isolated: bool = True,
) -> PdfRenderResult:
    """Renderuje wszystkie strony PDF. Domyślnie w procesie potomnym z limitem czasu na stronę.

    Każdy błąd dokumentu lub strony kończy się ``RenderError`` z kodem; nie ma wyniku częściowego.
    """
    if not isolated:
        return _collect(iter_pdf_pages(data, cfg, source))
    payload = (data, cfg.model_dump(), source.value)
    items: list[PdfInfo | tuple[PageMeta, ImageArray]] = []
    try:
        for item in run_isolated(
            _isolated_target,
            payload,
            item_timeout_s=cfg.page_timeout_s,
            startup_timeout_s=cfg.worker_startup_timeout_s,
        ):
            items.append(item)
    except IsolationFailure as exc:
        where = "open" if exc.step <= 1 else f"page={exc.step - 1}"
        raise RenderError(exc.code, f"{where} {exc.detail}") from None
    return _collect(iter(items))


def _isolated_target(payload: tuple[bytes, dict[str, object], str]) -> Iterator[object]:
    data, cfg_dump, source = payload
    yield from iter_pdf_pages(data, RenderConfig.model_validate(cfg_dump), PageSource(source))


def _collect(items: Iterator[PdfInfo | tuple[PageMeta, ImageArray]]) -> PdfRenderResult:
    first = next(items, None)
    if not isinstance(first, PdfInfo):
        raise RenderError(IssueCode.PDF_UNREADABLE, "no document info")
    pages: list[RenderedPage] = []
    for item in items:
        if not isinstance(item, tuple):
            raise RenderError(IssueCode.PDF_UNREADABLE, "unexpected item")
        meta, image = item
        pages.append(RenderedPage(meta=meta, image=image))
    if len(pages) != first.page_count:
        raise RenderError(
            IssueCode.PDF_UNREADABLE, f"pages={len(pages)} expected={first.page_count}"
        )
    return PdfRenderResult(info=first, pages=tuple(pages))
