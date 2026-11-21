"""Tymczasowe modele kanału wizualnego: wyjście renderera i elementy wizualne kroku 3.

TODO(#17): do uzgodnienia i przeniesienia do ``backend/shared/schemas``.
``BoundingBox`` i ``Severity`` pochodzą już z #17 (``backend.shared.schemas.quality``)
i nie są tu duplikowane.

Konwencje (opis w ``docs/vision.md``):
- numeracja stron od 1 (jak ``FieldSource.page`` w #17),
- rozmiary w pikselach odnoszą się do obrazu strony po uwzględnieniu ``/Rotate`` lub EXIF,
- obraz strony (tablica numpy) nie jest częścią serializowanych metadanych.
"""

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Literal

import numpy as np
import numpy.typing as npt
from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.shared.schemas.quality import BoundingBox, Fraction, Severity
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


# --- Elementy wizualne strony: kontrakt wyjścia kroku 3 (tymczasowy, TODO(#17)) ---


class ElementKind(StrEnum):
    """Kategoria elementu wizualnego (opis i przypadki graniczne: ``eval/visual/LABELING.md``)."""

    CHAR_BOXES = "char_boxes"  # ciąg przylegających kratek na znaki (liczba kratek w ``cells``)
    CHECKBOX = "checkbox"  # pojedyncze pole wyboru
    CHECKBOX_GROUP = "checkbox_group"  # grupa powiązanych pól wyboru
    TEXT_LINE = "text_line"  # linia ciągła do wpisywania
    DOTTED_LINE = "dotted_line"  # ciąg kropek do wpisywania
    TABLE_CELL = "table_cell"  # zamknięty prostokąt na tekst (komórka tabeli lub ramka pola)
    SECTION_FRAME = "section_frame"  # duża ramka obejmująca sekcję lub tabelę
    SIGNATURE = "signature"  # miejsce na podpis
    OFFICE_ONLY = "office_only"  # miejsce wypełniane przez urząd


class ElementRole(StrEnum):
    """Rola elementu w formularzu."""

    INPUT = "input"  # miejsce na wartość wpisywaną przez wnioskodawcę
    CHOICE = "choice"  # wybór (pole wyboru lub grupa)
    SKIP = "skip"  # nie jest polem wnioskodawcy (etykieta, nagłówek, część urzędu, kontener)
    E_SIGNATURE = "e_signature"  # podpis
    SECTION = "section"  # ramka sekcji
    UNKNOWN = "unknown"  # rola nieustalona (tylko wynik detekcji; w etykietach niedozwolona)


class ElementSource(StrEnum):
    OPENCV = "opencv"
    VLM = "vlm"
    BOTH = "both"


ELEMENT_ID_PATTERN = r"^(cv|vlm)-p([1-9][0-9]*)-([0-9]{3,})$"
"""ID elementu: ``cv-p{strona}-{nnn}`` (OpenCV, także po potwierdzeniu przez VLM)
lub ``vlm-p{strona}-{nnn}`` (element dodany przez VLM)."""

ReasonCode = Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9_]*$", max_length=64)]
ElementId = Annotated[str, Field(pattern=ELEMENT_ID_PATTERN)]


def has_area(bbox: BoundingBox) -> bool:
    """Czy prostokąt ma dodatnią szerokość i wysokość (element zajmuje co najmniej 1 piksel)."""
    return bbox.x1 > bbox.x0 and bbox.y1 > bbox.y0


def check_kind_attributes(
    kind: ElementKind,
    cells: int | None,
    options: tuple[str, ...],
    multi: bool | None,
    element_id: str,
) -> None:
    """Reguły atrybutów zależnych od kategorii, wspólne dla wyników detekcji i etykiet #12.

    ``cells`` jest wymagane dla ``char_boxes`` i niedozwolone dla innych kategorii;
    ``options`` i ``multi`` dotyczą tylko ``checkbox_group``.
    """
    if (kind is ElementKind.CHAR_BOXES) != (cells is not None):
        raise ValueError("cells jest wymagane dla char_boxes i niedozwolone dla innych kategorii")
    if kind is not ElementKind.CHECKBOX_GROUP and (options or multi is not None):
        raise ValueError("options i multi dotyczą tylko checkbox_group")
    if len(set(options)) != len(options):
        raise ValueError("options zawierają powtórzenia")
    if element_id in options:
        raise ValueError("grupa nie może zawierać samej siebie")


class VisualElement(VisionModel):
    """Element wizualny strony (brief 7.5).

    ``options`` grupy wyboru to ID należących do niej elementów ``checkbox`` w kolejności czytania;
    etykieta opcji to ``label_block_id`` danego checkboxa.
    """

    id: ElementId
    page: Annotated[int, Field(ge=1)]
    kind: ElementKind
    bbox: BoundingBox
    cells: Annotated[int, Field(ge=1)] | None = None
    label_block_id: Annotated[str, Field(min_length=1, max_length=64)] | None = None
    role: ElementRole = ElementRole.UNKNOWN
    options: tuple[ElementId, ...] = ()
    multi: bool | None = None
    source: ElementSource
    confidence: Fraction
    confidence_reasons: tuple[ReasonCode, ...] = ()

    @model_validator(mode="after")
    def _consistent(self) -> VisualElement:
        match = re.match(ELEMENT_ID_PATTERN, self.id)
        if match is None:  # pragma: no cover - wzorzec sprawdzony przez pole
            raise ValueError("niepoprawne id elementu")
        if int(match.group(2)) != self.page:
            raise ValueError("numer strony w id różni się od pola page")
        if (match.group(1) == "vlm") != (self.source is ElementSource.VLM):
            raise ValueError("id vlm-* wtedy i tylko wtedy, gdy source=vlm")
        if not has_area(self.bbox):
            raise ValueError("bbox musi mieć dodatnią szerokość i wysokość")
        check_kind_attributes(self.kind, self.cells, self.options, self.multi, self.id)
        for option in self.options:
            option_match = re.match(ELEMENT_ID_PATTERN, option)
            if option_match is None or int(option_match.group(2)) != self.page:
                raise ValueError("options muszą wskazywać elementy tej samej strony")
        return self
