"""Renderowanie dokumentów do obrazów stron (#10).

Punkt wejścia: ``render_document``. Ścieżka jest wybierana według rodzaju **wykrytego
z zawartości** (``detect.detect_kind``), a nie według rozszerzenia; niezgodność z rodzajem
deklarowanym to ostrzeżenie ``DECLARED_KIND_MISMATCH`` w metadanych dokumentu (decyzja 10).

Moduł nie zależy od detektorów ani VLM, żeby mógł zostać przeniesiony do #7 lub podmieniony.
"""

import hashlib

from backend.worker.vision.config import RenderConfig
from backend.worker.vision.errors import IssueCode, RenderError
from backend.worker.vision.models import (
    DocumentKind,
    DocumentMeta,
    PageSource,
    RenderedDocument,
    RenderIssue,
)
from backend.worker.vision.render.detect import declared_kind_issues, detect_kind
from backend.worker.vision.render.image import render_image
from backend.worker.vision.render.office import OfficeConverter, default_office_converter
from backend.worker.vision.render.pdf import render_pdf

__all__ = ["render_document"]


def render_document(
    data: bytes,
    declared_kind: DocumentKind | None,
    cfg: RenderConfig,
    *,
    office: OfficeConverter | None = None,
    isolated: bool = True,
) -> RenderedDocument:
    """Renderuje dokument do stron RGB z metadanymi.

    ``declared_kind``: rodzaj z kroku 1 (np. z rozszerzenia) albo ``None``.
    ``office``: konwerter dokumentów biurowych; domyślnie według ``cfg.office_enabled``.
    Błędy dokumentu zgłasza jako ``RenderError`` z kodem; nie ma wyniku częściowego.
    """
    detection = detect_kind(data)
    if detection.kind is None:
        raise RenderError(IssueCode.UNSUPPORTED_FORMAT, f"detected={detection.detail}")
    kind = detection.kind
    issues: list[RenderIssue] = declared_kind_issues(declared_kind, detection)

    if kind is DocumentKind.PDF:
        pdf = render_pdf(data, cfg, PageSource.NATIVE_PDF, isolated=isolated)
        pages, versions, source_pages = pdf.pages, pdf.info.renderer_versions, pdf.info.page_count
    elif kind.is_image:
        image = render_image(data, kind, cfg, isolated=isolated)
        pages, versions = image.pages, image.info.renderer_versions
        source_pages = 1  # jeden obraz (pierwsza klatka), niezależnie od liczby stron po cięciu
        issues.extend(image.info.issues)
    else:
        converter = office if office is not None else default_office_converter(cfg)
        converted = converter.convert_to_pdf(data, kind)
        pdf = render_pdf(converted, cfg, PageSource.CONVERTED, isolated=isolated)
        pages, source_pages = pdf.pages, pdf.info.page_count
        versions = {**pdf.info.renderer_versions, **converter.versions()}

    meta = DocumentMeta(
        sha256=hashlib.sha256(data).hexdigest(),
        declared_kind=declared_kind,
        detected_kind=kind,
        source_page_count=source_pages,
        renderer_versions=versions,
        issues=tuple(issues),
    )
    return RenderedDocument(meta=meta, pages=pages)
