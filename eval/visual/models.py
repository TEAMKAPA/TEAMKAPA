"""Modele zbioru oznaczeń kanału wizualnego (#12): podział, etykiety stron i manifest.

Pliki (opis w ``eval/visual/README.md``):

- ``eval/visual/split.json``: podział ``dev``/``holdout`` po dokumentach (rodzajach wniosku),
  zarejestrowany przed Etapem C (``teamkapa.vision.split/1``),
- ``dane/robocze/labels/<label_id>.json``: etykiety jednej strony (``teamkapa.vision.labels/1``).
  Zawierają układ formularza, więc nie trafiają do repozytorium,
- manifest (``teamkapa.vision.manifest/1``): wersja publiczna bez ścieżek plików w
  ``eval/visual/manifest.json``, pełna (ze ścieżkami względem ``dane/``) tylko w ``dane/robocze/``.

Współrzędne: ``BoundingBox`` z #17 (ułamki strony, początek w lewym górnym rogu), jak w kroku 3.
Modele są niezmienne i odrzucają nieznane pola; niespójność danych to błąd walidacji, nigdy
ciche pominięcie.
"""

import re
from collections import Counter
from collections.abc import Mapping
from datetime import date
from enum import StrEnum
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.shared.schemas.quality import BoundingBox, Fraction
from backend.worker.vision.models import (
    DocumentKind,
    ElementKind,
    ElementRole,
    PageSource,
    ReasonCode,
    check_kind_attributes,
    has_area,
)

SPLIT_SCHEMA = "teamkapa.vision.split/1"
LABELS_SCHEMA = "teamkapa.vision.labels/1"
MANIFEST_SCHEMA = "teamkapa.vision.manifest/1"

DOC_ID_PATTERN = r"^[a-z]+[0-9]{2,}$"
LABEL_ID_PATTERN = r"^([a-z]+[0-9]{2,})(?:-[a-z0-9]+)*-p([0-9]{2,})$"
GT_ID_PATTERN = r"^gt-p([1-9][0-9]*)-([0-9]{3,})$"

Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
FolderNo = Annotated[str, Field(pattern=r"^[1-9][0-9]*$")]
DocId = Annotated[str, Field(pattern=DOC_ID_PATTERN)]
FileId = Annotated[str, Field(pattern=r"^[a-z]+[0-9]{2,}(?:-[a-z0-9]+)+$")]
LabelId = Annotated[str, Field(pattern=LABEL_ID_PATTERN)]
GtId = Annotated[str, Field(pattern=GT_ID_PATTERN)]
PageNo = Annotated[int, Field(ge=1)]
PositiveInt = Annotated[int, Field(ge=1)]
Dpi = Annotated[float, Field(gt=0)]


def coi_doc_id(folder: str) -> str:
    """ID dokumentu dla folderu materiałów COI: ``"1"`` → ``"f01"``."""
    return f"f{int(folder):02d}"


def label_id_for(prefix: str, page: int) -> str:
    """ID zestawu etykiet strony: ``label_id_for("f05-png", 1)`` → ``"f05-png-p01"``."""
    return f"{prefix}-p{page:02d}"


def _label_id_parts(label_id: str) -> tuple[str, int]:
    match = re.match(LABEL_ID_PATTERN, label_id)
    if match is None:
        raise ValueError(f"niepoprawne label_id: {label_id}")
    return match.group(1), int(match.group(2))


def _gt_page(element_id: str) -> int:
    match = re.match(GT_ID_PATTERN, element_id)
    if match is None:
        raise ValueError(f"niepoprawne id elementu: {element_id}")
    return int(match.group(1))


class EvalModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


# --- Podział dev / holdout ---


class Split(StrEnum):
    DEV = "dev"  # strojenie progów i kalibracja
    HOLDOUT = "holdout"  # wyłącznie raport końcowy


class SplitDefinition(EvalModel):
    """Podział po dokumentach: wszystkie strony i warianty formatu dokumentu są w jednej części."""

    schema_version: Literal["teamkapa.vision.split/1"]
    registered: date
    rule: Annotated[str, Field(min_length=1)]
    dev: tuple[DocId, ...]
    holdout: tuple[DocId, ...]
    pilot: tuple[LabelId, ...] = ()

    @model_validator(mode="after")
    def _consistent(self) -> SplitDefinition:
        if not self.dev or not self.holdout:
            raise ValueError("obie części podziału muszą zawierać dokumenty")
        docs = self.dev + self.holdout
        if len(set(docs)) != len(docs):
            raise ValueError("dokument występuje w podziale więcej niż raz")
        for label_id in self.pilot:
            doc_id, _ = _label_id_parts(label_id)
            if doc_id not in self.dev:
                raise ValueError("strony pilota muszą należeć do części dev")
        return self

    def split_of(self, doc_id: str) -> Split:
        """Część, do której należy dokument; dokument bez przypisania to błąd."""
        if doc_id in self.dev:
            return Split.DEV
        if doc_id in self.holdout:
            return Split.HOLDOUT
        raise ValueError(f"dokument {doc_id} nie ma przypisanej części podziału")


# --- Etykiety strony ---


class LabelStatus(StrEnum):
    COMPLETE = "complete"  # strona oznaczona w całości
    EMPTY = "empty"  # strona sprawdzona, nie ma na niej elementów


class RoleOrigin(StrEnum):
    HUMAN = "human"  # rola wybrana przez oznaczającego
    DEFAULT = "default"  # rola domyślna kategorii (DEFAULT_ROLES, LABELING.md)
    INFERRED = "inferred"  # reguła konwertera (np. komórka zawierająca inny element do wpisywania)


DEFAULT_ROLES: Mapping[ElementKind, ElementRole] = MappingProxyType(
    {
        ElementKind.CHAR_BOXES: ElementRole.INPUT,
        ElementKind.CHECKBOX: ElementRole.CHOICE,
        ElementKind.CHECKBOX_GROUP: ElementRole.CHOICE,
        ElementKind.TEXT_LINE: ElementRole.INPUT,
        ElementKind.DOTTED_LINE: ElementRole.INPUT,
        ElementKind.TABLE_CELL: ElementRole.INPUT,
        ElementKind.SECTION_FRAME: ElementRole.SECTION,
        ElementKind.SIGNATURE: ElementRole.E_SIGNATURE,
        ElementKind.OFFICE_ONLY: ElementRole.SKIP,
    }
)
"""Rola przyjmowana, gdy oznaczający jej nie wybrał (``role_origin=default``)."""

_FILL = (ElementRole.INPUT, ElementRole.SKIP)
_CHOOSE = (ElementRole.CHOICE, ElementRole.SKIP)
ALLOWED_ROLES: Mapping[ElementKind, frozenset[ElementRole]] = MappingProxyType(
    {
        ElementKind.CHAR_BOXES: frozenset(_FILL),
        ElementKind.CHECKBOX: frozenset(_CHOOSE),
        ElementKind.CHECKBOX_GROUP: frozenset(_CHOOSE),
        ElementKind.TEXT_LINE: frozenset(_FILL),
        ElementKind.DOTTED_LINE: frozenset(_FILL),
        ElementKind.TABLE_CELL: frozenset(_FILL),
        ElementKind.SECTION_FRAME: frozenset({ElementRole.SECTION}),
        ElementKind.SIGNATURE: frozenset({ElementRole.E_SIGNATURE, ElementRole.SKIP}),
        ElementKind.OFFICE_ONLY: frozenset({ElementRole.SKIP}),
    }
)
"""Role zgodne z kategorią (LABELING.md). Np. kropki pod „podpis” to kategoria ``signature``,
a nie ``dotted_line`` z rolą ``e_signature``; miejsce urzędu to ``office_only``."""


class LabelElement(EvalModel):
    """Element oznaczony ręcznie; ``options`` grupy to ID jej checkboxów (jak w kroku 3)."""

    id: GtId
    kind: ElementKind
    bbox: BoundingBox
    role: ElementRole
    role_origin: RoleOrigin
    cells: PositiveInt | None = None
    multi: bool | None = None
    options: tuple[GtId, ...] = ()
    uncertain: bool = False
    ls_region_id: Annotated[str, Field(min_length=1, max_length=64)] | None = None

    @property
    def page(self) -> int:
        return _gt_page(self.id)

    @model_validator(mode="after")
    def _consistent(self) -> LabelElement:
        if self.role is ElementRole.UNKNOWN:
            raise ValueError("etykieta musi mieć ustaloną rolę (unknown jest niedozwolone)")
        if self.role not in ALLOWED_ROLES[self.kind]:
            raise ValueError(f"rola {self.role.value} niedozwolona dla kategorii {self.kind.value}")
        if not has_area(self.bbox):
            raise ValueError("bbox musi mieć dodatnią szerokość i wysokość")
        check_kind_attributes(self.kind, self.cells, self.options, self.multi, self.id)
        if self.kind is ElementKind.CHECKBOX_GROUP and len(self.options) < 2:
            raise ValueError("checkbox_group musi mieć co najmniej 2 checkboxy")
        if any(_gt_page(option) != self.page for option in self.options):
            raise ValueError("options muszą wskazywać elementy tej samej strony")
        return self


class LabelSource(EvalModel):
    """Obraz, na którym oznaczano: strona ``page`` pliku ``file_id`` po renderowaniu."""

    file_id: FileId
    sha256: Sha256  # plik źródłowy
    page: PageNo
    width_px: PositiveInt
    height_px: PositiveInt
    dpi: Dpi | None
    image_sha256: Sha256  # piksele oznaczanego obrazu (``geometry.image_sha256``)


class LabelTool(EvalModel):
    name: Annotated[str, Field(min_length=1)]
    version: Annotated[str, Field(min_length=1)]
    config_sha256: Sha256


class PageLabels(EvalModel):
    """Etykiety jednej strony (plik ``dane/robocze/labels/<label_id>.json``)."""

    schema_version: Literal["teamkapa.vision.labels/1"]
    label_id: LabelId
    doc_id: DocId
    source: LabelSource
    status: LabelStatus
    annotator: Annotated[str, Field(pattern=r"^human-[1-9][0-9]*$")]
    tool: LabelTool
    elements: tuple[LabelElement, ...] = ()

    @model_validator(mode="after")
    def _consistent(self) -> PageLabels:
        doc_id, page = _label_id_parts(self.label_id)
        if doc_id != self.doc_id or not self.source.file_id.startswith(f"{self.doc_id}-"):
            raise ValueError("label_id i source.file_id muszą należeć do dokumentu doc_id")
        if page != self.source.page:
            raise ValueError("numer strony w label_id różni się od source.page")
        if (self.status is LabelStatus.EMPTY) != (not self.elements):
            raise ValueError("status empty wtedy i tylko wtedy, gdy strona nie ma elementów")
        ids = [e.id for e in self.elements]
        if len(set(ids)) != len(ids):
            raise ValueError("powtórzone id elementów")
        if any(e.page != self.source.page for e in self.elements):
            raise ValueError("numer strony w id elementu różni się od source.page")
        kinds = {e.id: e.kind for e in self.elements}
        members = Counter(o for e in self.elements for o in e.options)
        for option, count in members.items():
            if kinds.get(option) is not ElementKind.CHECKBOX:
                raise ValueError("options grupy muszą wskazywać checkboxy tej strony")
            if count > 1:
                raise ValueError("checkbox należy do więcej niż jednej grupy")
        return self


# --- Manifest ---


class FileOrigin(StrEnum):
    COI = "coi"  # materiały konkursowe (dane/coi)
    OWN = "own"  # własne skany i zdjęcia wydruków
    BIP = "bip"  # formularze pobrane z BIP (dane/bip)


class FileStatus(StrEnum):
    RENDERED = "rendered"  # wyrenderowany, strony mogą wejść do zbioru
    EXCLUDED = "excluded"  # poza zbiorem; powód w ``code``
    REFERENCE = "reference"  # materiał referencyjny (np. oczekiwany JSON)


class ManifestFile(EvalModel):
    """Plik dokumentu. ``path`` (względem ``dane/``) występuje tylko w pełnym manifeście."""

    file_id: FileId
    origin: FileOrigin
    format: Annotated[str, Field(pattern=r"^[a-z0-9]{1,8}$")]  # rozszerzenie, małe litery
    sha256: Sha256
    size_bytes: Annotated[int, Field(ge=0)]
    declared_kind: DocumentKind | None
    detected_kind: DocumentKind | None
    status: FileStatus
    code: ReasonCode | None = None
    page_count: PositiveInt | None = None
    page_source: PageSource | None = None
    issues: tuple[ReasonCode, ...] = ()
    path: str | None = None

    @model_validator(mode="after")
    def _consistent(self) -> ManifestFile:
        rendered = self.status is FileStatus.RENDERED
        if rendered and (
            self.page_count is None or self.detected_kind is None or self.page_source is None
        ):
            raise ValueError("plik wyrenderowany wymaga page_count, detected_kind i page_source")
        if rendered and self.code is not None:
            raise ValueError("plik wyrenderowany nie ma kodu wykluczenia")
        if not rendered and (
            self.code is None or self.page_count is not None or self.page_source is not None
        ):
            raise ValueError("plik niewyrenderowany wymaga kodu i nie ma stron")
        if self.path is not None:
            pure = PurePosixPath(self.path)
            if "\\" in self.path or pure.is_absolute() or ".." in pure.parts or not pure.parts:
                raise ValueError("path musi być względną ścieżką POSIX bez '..'")
        return self


class GeometryCheck(EvalModel):
    """Zgodność wariantu strony z obrazem oznaczanym (``geometry.compare_pages``)."""

    size_equal: bool
    shift_px: tuple[int, int]  # przesunięcie treści wariantu (dx, dy), dodatnie: w prawo, w dół
    variant_ink_near_base: Fraction  # tusz wariantu w tolerancji od tuszu obrazu oznaczanego
    base_ink_near_variant: Fraction
    passed: bool


class ManifestVariant(EvalModel):
    """Strona pliku oceniana z etykietami danej strony. Plik oznaczany ma ``check = None``."""

    file_id: FileId
    page: PageNo
    check: GeometryCheck | None = None
    included: bool
    reason: ReasonCode | None = None

    @model_validator(mode="after")
    def _consistent(self) -> ManifestVariant:
        if self.included == (self.reason is not None):
            raise ValueError("reason wtedy i tylko wtedy, gdy wariant jest wykluczony")
        if self.included and self.check is not None and not self.check.passed:
            raise ValueError("wariant po nieudanej kontroli geometrii nie może być włączony")
        return self


class LabelState(StrEnum):
    PENDING = "pending"  # jeszcze nieoznaczona
    COMPLETE = "complete"
    EMPTY = "empty"


class ManifestPage(EvalModel):
    """Zestaw etykiet jednej strony i strony plików, które są z nim oceniane."""

    label_id: LabelId
    base_file_id: FileId
    page: PageNo
    width_px: PositiveInt
    height_px: PositiveInt
    dpi: Dpi | None
    image_sha256: Sha256
    label_state: LabelState = LabelState.PENDING
    labels_sha256: Sha256 | None = None
    element_counts: dict[ElementKind, Annotated[int, Field(ge=0)]] = Field(default_factory=dict)
    variants: tuple[ManifestVariant, ...]

    @model_validator(mode="after")
    def _consistent(self) -> ManifestPage:
        _, page = _label_id_parts(self.label_id)
        if page != self.page:
            raise ValueError("numer strony w label_id różni się od page")
        if (self.label_state is LabelState.PENDING) != (self.labels_sha256 is None):
            raise ValueError("labels_sha256 wtedy i tylko wtedy, gdy strona jest oznaczona")
        total = sum(self.element_counts.values())
        if self.label_state is LabelState.COMPLETE and total == 0:
            raise ValueError("strona complete musi mieć elementy")
        if self.label_state is not LabelState.COMPLETE and total != 0:
            raise ValueError("strona pending lub empty nie ma liczności elementów")
        keys = [(v.file_id, v.page) for v in self.variants]
        if len(set(keys)) != len(keys):
            raise ValueError("powtórzony wariant strony")
        base = [v for v in self.variants if (v.file_id, v.page) == (self.base_file_id, self.page)]
        if len(base) != 1 or base[0].check is not None or not base[0].included:
            raise ValueError("strona oznaczana musi być włączonym wariantem bez kontroli")
        if any(v.check is None for v in self.variants if v is not base[0]):
            raise ValueError("każdy wariant poza stroną oznaczaną wymaga kontroli geometrii")
        return self


class ManifestDocument(EvalModel):
    """Dokument (rodzaj wniosku): pliki we wszystkich formatach i jego strony do oceny."""

    doc_id: DocId
    folder: FolderNo | None = None
    split: Split
    files: tuple[ManifestFile, ...]
    pages: tuple[ManifestPage, ...] = ()

    @property
    def reference_json(self) -> bool:
        return any(f.status is FileStatus.REFERENCE and f.format == "json" for f in self.files)

    @model_validator(mode="after")
    def _consistent(self) -> ManifestDocument:
        if self.folder is not None and coi_doc_id(self.folder) != self.doc_id:
            raise ValueError("doc_id niezgodne z numerem folderu")
        file_ids = [f.file_id for f in self.files]
        if len(set(file_ids)) != len(file_ids):
            raise ValueError("powtórzone file_id")
        if any(not fid.startswith(f"{self.doc_id}-") for fid in file_ids):
            raise ValueError("file_id musi zaczynać się od doc_id")
        label_ids = [p.label_id for p in self.pages]
        if len(set(label_ids)) != len(label_ids):
            raise ValueError("powtórzone label_id")
        if any(_label_id_parts(lid)[0] != self.doc_id for lid in label_ids):
            raise ValueError("label_id musi należeć do dokumentu")
        page_counts = {
            f.file_id: f.page_count
            for f in self.files
            if f.status is FileStatus.RENDERED and f.page_count is not None
        }
        used: set[tuple[str, int]] = set()
        for page in self.pages:
            for variant in page.variants:
                count = page_counts.get(variant.file_id)
                if count is None or variant.page > count:
                    raise ValueError("wariant wskazuje nieistniejącą stronę pliku tego dokumentu")
                key = (variant.file_id, variant.page)
                if variant.included and key in used:
                    raise ValueError(
                        "strona pliku przypisana do więcej niż jednego zestawu etykiet"
                    )
                if variant.included:
                    used.add(key)
        return self


class RendererInfo(EvalModel):
    commit: Annotated[str, Field(pattern=r"^[0-9a-f]{7,40}$")]
    dirty: bool  # niezacommitowane zmiany w repozytorium w chwili renderowania
    dpi: Annotated[int, Field(ge=72, le=600)]
    versions: dict[str, str]


class LabelingInfo(EvalModel):
    tool: Annotated[str, Field(min_length=1)]
    version: Annotated[str, Field(min_length=1)]
    image: str | None = None  # obraz Dockera z digestem
    config_sha256: Sha256


class Manifest(EvalModel):
    schema_version: Literal["teamkapa.vision.manifest/1"]
    created: date
    split_registered: date
    split_rule: Annotated[str, Field(min_length=1)]
    split_frozen: date | None = None
    renderer: RendererInfo
    labeling: LabelingInfo
    documents: tuple[ManifestDocument, ...]
    reproduce: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _consistent(self) -> Manifest:
        doc_ids = [d.doc_id for d in self.documents]
        if len(set(doc_ids)) != len(doc_ids):
            raise ValueError("powtórzone doc_id")
        rendered = [
            f.sha256 for d in self.documents for f in d.files if f.status is FileStatus.RENDERED
        ]
        if len(set(rendered)) != len(rendered):
            raise ValueError("ten sam plik wyrenderowany występuje w zbiorze więcej niż raz")
        return self

    @property
    def is_public(self) -> bool:
        """Czy manifest nie zawiera ścieżek plików (wersja do repozytorium)."""
        return all(f.path is None for d in self.documents for f in d.files)

    def public(self) -> Manifest:
        """Kopia bez ścieżek plików (pliki identyfikują numer folderu, format i ``sha256``)."""
        documents = tuple(
            d.model_copy(
                update={"files": tuple(f.model_copy(update={"path": None}) for f in d.files)}
            )
            for d in self.documents
        )
        return self.model_copy(update={"documents": documents})

    def find_page(self, label_id: str) -> tuple[ManifestDocument, ManifestPage]:
        for document in self.documents:
            for page in document.pages:
                if page.label_id == label_id:
                    return document, page
        raise KeyError(label_id)


# --- Zapis i odczyt JSON ---

_PATH_FIELD = {"documents": {"__all__": {"files": {"__all__": {"path"}}}}}


def dump_json(model: BaseModel) -> str:
    """Deterministyczny JSON (kolejność pól jak w modelu, wcięcie 2, znak nowej linii na końcu)."""
    return model.model_dump_json(indent=2) + "\n"


def dump_public_manifest(manifest: Manifest) -> str:
    """JSON manifestu do repozytorium: bez pola ``path``."""
    return manifest.public().model_dump_json(indent=2, exclude=_PATH_FIELD) + "\n"


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def read_json[M: BaseModel](path: Path, model: type[M]) -> M:
    return model.model_validate_json(path.read_text(encoding="utf-8"))
