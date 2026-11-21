"""Tymczasowe modele wyjścia renderera.

TODO(#17): do uzgodnienia i przeniesienia do ``backend/shared/schemas``.
``BoundingBox`` i ``Severity`` pochodzą już z #17 (``backend.shared.schemas.quality``)
i nie są tu duplikowane.

Konwencje (opis w ``docs/vision.md``):
- numeracja stron od 1 (jak ``FieldSource.page`` w #17),
- rozmiary w pikselach odnoszą się do obrazu strony po uwzględnieniu ``/Rotate`` lub EXIF,
- obraz strony (tablica numpy) nie jest częścią serializowanych metadanych.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Literal

import numpy as np
import numpy.typing as npt
from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.shared.schemas.quality import Severity
from backend.worker.vision.errors import IssueCode

Rotation = Literal[0, 90, 180, 270]
PdfBox = tuple[float, float, float, float]
"""Prostokąt w punktach PDF: (x0, y0, x1, y1), początek w lewym dolnym rogu, x0 < x1, y0 < y1."""


class VisionModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class DocumentKind(StrEnum):
    """Obsługiwane rodzaje dokumentów wejściowych (lista z regulaminu)."""

    PDF = "pdf"
    DOC = "doc"
    DOCX = "docx"
    ODS = "ods"
    XLSX = "xlsx"
    JPEG = "jpeg"
    PNG = "png"

    @property
    def is_office(self) -> bool:
        return self in _OFFICE_KINDS

    @property
    def is_image(self) -> bool:
        return self in (DocumentKind.JPEG, DocumentKind.PNG)


_OFFICE_KINDS = frozenset(
    {DocumentKind.DOC, DocumentKind.DOCX, DocumentKind.ODS, DocumentKind.XLSX}
)


class PageSource(StrEnum):
    NATIVE_PDF = "native_pdf"  # strona PDF dostarczonego wprost
    CONVERTED = "converted"  # strona PDF powstałego z konwersji dokumentu biurowego
    IMAGE = "image"  # strona z pliku graficznego (całość lub część zszytego obrazu)


class DpiSource(StrEnum):
    RENDER = "render"  # DPI użyte przy renderowaniu PDF
    METADATA = "metadata"  # DPI zapisane w pliku graficznym
    UNKNOWN = "unknown"  # brak informacji (dpi = None)


class DpiEstimateSource(StrEnum):
    A4_WIDTH = "a4_width"  # szerokość strony przyjęta jako szerokość A4 (8,27 cala)


class RenderIssue(VisionModel):
    """Problem lub informacja z renderowania. ``detail`` bez treści dokumentu."""

    code: IssueCode
    severity: Severity
    detail: str = ""


class PageGeometry(VisionModel):
    """Geometria strony PDF w punktach (1/72 cala)."""

    mediabox: PdfBox
    cropbox: PdfBox
    rotation: Rotation = 0

    @model_validator(mode="after")
    def _valid_boxes(self) -> PageGeometry:
        for name, box in (("mediabox", self.mediabox), ("cropbox", self.cropbox)):
            if not (box[0] < box[2] and box[1] < box[3]):
                raise ValueError(f"{name}: wymagane x0 < x1 i y0 < y1")
        v = self.visible_box
        if not (v[0] < v[2] and v[1] < v[3]):
            raise ValueError("cropbox nie przecina się z mediabox")
        return self

    @property
    def visible_box(self) -> PdfBox:
        """Widoczny obszar strony: część wspólna CropBox i MediaBox (ISO 32000, 14.11.2)."""
        m, c = self.mediabox, self.cropbox
        return (max(m[0], c[0]), max(m[1], c[1]), min(m[2], c[2]), min(m[3], c[3]))

    @property
    def displayed_size_pt(self) -> tuple[float, float]:
        """(szerokość, wysokość) strony tak, jak jest wyświetlana, czyli po ``/Rotate``."""
        v = self.visible_box
        w, h = v[2] - v[0], v[3] - v[1]
        return (h, w) if self.rotation in (90, 270) else (w, h)


class PageOrigin(VisionModel):
    """Położenie strony w oryginalnym obrazie (dla stron wyciętych ze zszytego obrazu).

    Wartości w pikselach oryginału (po orientacji EXIF), niezależne od ewentualnego zmniejszenia
    strony (``DPI_REDUCED``): bbox w ułamkach strony nie zależy od rozdzielczości.
    """

    image_width_px: Annotated[int, Field(ge=1)]
    image_height_px: Annotated[int, Field(ge=1)]
    offset_y_px: Annotated[int, Field(ge=0)]
    part_height_px: Annotated[int, Field(ge=1)]
    part: Annotated[int, Field(ge=1)]
    parts: Annotated[int, Field(ge=1)]

    @model_validator(mode="after")
    def _consistent(self) -> PageOrigin:
        if self.part > self.parts:
            raise ValueError("part > parts")
        if self.offset_y_px + self.part_height_px > self.image_height_px:
            raise ValueError("część wychodzi poza oryginalny obraz")
        return self


class PageMeta(VisionModel):
    """Metadane strony (serializowalne: raport, manifest, logi)."""

    page: Annotated[int, Field(ge=1)]
    width_px: Annotated[int, Field(ge=1)]
    height_px: Annotated[int, Field(ge=1)]
    dpi: Annotated[float, Field(gt=0)] | None
    dpi_source: DpiSource
    dpi_estimate: Annotated[float, Field(gt=0)] | None = None
    dpi_estimate_source: DpiEstimateSource | None = None
    rotation: Rotation = 0
    exif_orientation: Annotated[int, Field(ge=1, le=8)] | None = None
    source: PageSource
    geometry: PageGeometry | None = None
    origin: PageOrigin | None = None
    issues: tuple[RenderIssue, ...] = ()

    @model_validator(mode="after")
    def _consistent(self) -> PageMeta:
        if (self.dpi is None) != (self.dpi_source is DpiSource.UNKNOWN):
            raise ValueError("dpi=None wtedy i tylko wtedy, gdy dpi_source=unknown")
        if (self.dpi_estimate is None) != (self.dpi_estimate_source is None):
            raise ValueError("dpi_estimate i dpi_estimate_source występują razem")
        if self.source is PageSource.IMAGE and self.geometry is not None:
            raise ValueError("strona z obrazu nie ma geometrii PDF")
        if self.source is not PageSource.IMAGE and (
            self.origin is not None or self.exif_orientation is not None
        ):
            raise ValueError("origin i exif_orientation dotyczą tylko stron z obrazu")
        return self


class DocumentMeta(VisionModel):
    """Metadane dokumentu. ``renderer_versions``: nazwa komponentu -> wersja (do manifestu #12)."""

    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    declared_kind: DocumentKind | None
    detected_kind: DocumentKind
    source_page_count: Annotated[int, Field(ge=1)]
    renderer_versions: dict[str, str] = Field(default_factory=dict)
    issues: tuple[RenderIssue, ...] = ()


ImageArray = npt.NDArray[np.uint8]
"""Obraz strony: RGB, ``uint8``, kształt (wysokość, szerokość, 3)."""


@dataclass(frozen=True, slots=True)
class RenderedPage:
    meta: PageMeta
    image: ImageArray

    def __post_init__(self) -> None:
        shape = self.image.shape
        if self.image.dtype != np.uint8 or len(shape) != 3 or shape[2] != 3:
            raise ValueError("obraz strony musi być RGB uint8 o kształcie (h, w, 3)")
        if (shape[1], shape[0]) != (self.meta.width_px, self.meta.height_px):
            raise ValueError("rozmiar obrazu niezgodny z metadanymi strony")


@dataclass(frozen=True, slots=True)
class RenderedDocument:
    meta: DocumentMeta
    pages: tuple[RenderedPage, ...]

    def __post_init__(self) -> None:
        numbers = [p.meta.page for p in self.pages]
        if numbers != list(range(1, len(numbers) + 1)):
            raise ValueError("strony muszą być numerowane kolejno od 1")
