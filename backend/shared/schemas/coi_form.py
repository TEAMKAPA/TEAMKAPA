"""Modele konfiguracji e-formularza COI (``FormDefinition``).

Źródła, w kolejności ważności przy sprzecznościach (patrz docs/coi-specyfikacja.md):
1. referencyjne konfiguracje od organizatora (``backend/worker/rag/catalog/forms/``) — tak
   wygląda plik, który system COI faktycznie eksportuje i importuje,
2. „Reguły techniczne konfiguracji formularzy i pól” — typy pól, walidatory, stany,
3. ``importowanyPlikJsonSchemat.json`` i „OpisImportowanegoPlikuJSON” — nazwy i typy kluczy,
4. „Dane inicjalne” — pola zasilane z rejestrów.

Format zgodny z plikami referencyjnymi COI:
- klucz pól sekcji to ``repeatingSectionGrid`` (schemat ma literówkę ``repetingSectionGrid`` —
  przyjmujemy ją na wejściu, nigdy nie zapisujemy),
- każde pole, także sekcja, ma komplet kluczy wspólnych (``title``, ``description``,
  ``options``, ``validation``, ``readOnly``, ``repeatingSectionGrid``, ``states``,
  ``questionOrder``, ``tooltipMessage``) — puste, gdy nie dotyczą typu,
- ``defaultValue`` to tekst albo lista (np. ``["Polska"]`` dla słownika „Kraje”),
- identyfikator opcji to jej nazwa; identyfikator przejścia ``flow`` jest dowolnym tekstem.

Reguły strukturalne wyrażone typami (nie da się ich złamać poprawnym obiektem):
- strona zawiera wyłącznie sekcje, a sekcja wyłącznie pola — bez zagnieżdżania sekcji,
- opcje mają tylko pola wyboru, walidatory poza REQUIRED tylko TEXT_FIELD, sekcja nie ma
  walidatorów, stany sekcji i bloku tekstu to wyłącznie SHOW,
- pola wejściowe mają jawnie określoną obligatoryjność (klucz ``validation``).
Reguły dotyczące całego formularza (strony, przepływ, JSONata, unikalność) sprawdza
``backend.shared.validators.form_rules``.
"""

from collections.abc import Sequence
from enum import StrEnum
from typing import Annotated, Any, ClassVar, Literal
from uuid import UUID, uuid4

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)
from pydantic.alias_generators import to_camel

MAX_FORM_TITLE_LENGTH = 255

# Nazwa techniczna trafia do zapytań JSONata w apostrofach — bez spacji i cudzysłowów.
TechName = Annotated[str, StringConstraints(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$", max_length=100)]
NonEmptyStr = Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)]
#: Wartość domyślna: tekst (np. ``"PESEL.name"``) albo lista (wybór ze słownika: ``["Polska"]``).
DefaultValue = str | list[str]

#: Pusty opis sekcji i opisy w HTML — tak zapisuje je edytor COI.
EMPTY_HTML = "<html><head></head><body></body></html>"

GRID_KEY = "repeatingSectionGrid"
_GRID_ALIASES = AliasChoices(GRID_KEY, "repetingSectionGrid", "repeating_section_grid")

#: Kolejność kluczy pola jak w plikach eksportowanych przez COI (ułatwia porównanie).
FIELD_KEY_ORDER = (
    "id", "type", "techName", "title", "description", "dictionaryName", "options",
    "multiLevelTree", "validation", "searchEnabled", "selectAllEnabled", "readOnly", GRID_KEY,
    "defaultValue", "defaultValueType", "consentSectionConfig", "states", "questionOrder",
    "tooltipMessage",
)  # fmt: skip
QUESTION_KEY_ORDER = ("x", "y", "rows", "cols", "minItemRows", "field")


class TabType(StrEnum):
    MAIN = "MAIN"
    FINISH = "FINISH"


class FieldType(StrEnum):
    SECTION = "SECTION"
    REPEATING_SECTION = "REPEATING_SECTION"
    TEXT_FIELD = "TEXT_FIELD"
    CHECKBOX = "CHECKBOX"
    NUMBER = "NUMBER"
    CONSENT_SECTION = "CONSENT_SECTION"
    SELECT = "SELECT"
    TEXTAREA = "TEXTAREA"
    FILE_UPLOADER = "FILE_UPLOADER"
    RADIO = "RADIO"
    DATEPICKER = "DATEPICKER"
    TEXT_BLOCK = "TEXT_BLOCK"
    INFO_SECTION = "INFO_SECTION"


class Validator(StrEnum):
    REQUIRED = "REQUIRED"
    EMAIL = "EMAIL"
    PESEL = "PESEL"
    NIP = "NIP"
    REGON = "REGON"
    ZIP_CODE = "ZIP_CODE"
    EPUAP = "EPUAP"
    ADE = "ADE"
    SERIAL_NUMBER_ID_CARD = "SERIAL_NUMBER_ID_CARD"


class StateType(StrEnum):
    SHOW = "SHOW"
    READ_ONLY = "READ_ONLY"


class DefaultValueType(StrEnum):
    EXTERNAL_SYSTEM = "EXTERNAL_SYSTEM"


# Typy pól przyjmujących dane od użytkownika — muszą mieć określoną obligatoryjność.
INPUT_FIELD_TYPES = frozenset(
    {
        FieldType.TEXT_FIELD,
        FieldType.CHECKBOX,
        FieldType.NUMBER,
        FieldType.SELECT,
        FieldType.TEXTAREA,
        FieldType.FILE_UPLOADER,
        FieldType.RADIO,
        FieldType.DATEPICKER,
    }
)
SECTION_TYPES = frozenset({FieldType.SECTION, FieldType.REPEATING_SECTION})
#: Pola wyłącznie informacyjne (dozwolone na stronie FINISH).
INFORMATIONAL_FIELD_TYPES = frozenset({FieldType.TEXT_BLOCK, FieldType.INFO_SECTION})

# Pola wyboru poza TEXT_FIELD mogą mieć wyłącznie walidator wymagalności.
RequiredOnly = list[Literal[Validator.REQUIRED]]


class CoiModel(BaseModel):
    """Baza: klucze JSON w camelCase dokładnie jak w plikach COI, nieznane klucze odrzucane."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
        use_enum_values=False,
        str_strip_whitespace=True,
    )


# ---------------------------------------------------------------- elementy wspólne


class Option(CoiModel):
    """Opcja pola wyboru. W COI identyfikatorem opcji jest jej nazwa."""

    id: NonEmptyStr
    name: NonEmptyStr
    # COI toleruje brak (zdarza się w eksporcie); generator zawsze ją ustawia.
    tech_name: TechName | None = None

    @model_validator(mode="before")
    @classmethod
    def _id_defaults_to_name(cls, data: Any) -> Any:
        if isinstance(data, dict) and not data.get("id") and data.get("name"):
            return {**data, "id": data["name"]}
        return data


class State(CoiModel):
    """Stan dynamiczny: warunek JSONata, przy którym element jest pokazany / tylko do odczytu."""

    type: StateType
    query: NonEmptyStr


class ShowState(State):
    """Sekcje i bloki tekstu mają wyłącznie stan SHOW."""

    type: Literal[StateType.SHOW]


SectionState = ShowState


class Consent(CoiModel):
    description: NonEmptyStr
    required: bool | None = None


class ConsentSectionConfig(CoiModel):
    consents: Annotated[list[Consent], Field(min_length=1)]
    required: bool | None = None
    files: list[str] = Field(default_factory=list)


class BaseField(CoiModel):
    """Klucze wspólne wszystkich pól — COI zapisuje je w każdym polu, także pustym."""

    #: Czy typ przyjmuje opcje (pola wyboru).
    _choice: ClassVar[bool] = False

    id: UUID = Field(default_factory=uuid4)
    tech_name: TechName
    title: str = ""
    description: str = ""
    options: list[Option] = Field(default_factory=list)
    # Sequence: typy pól zawężają dozwolone walidatory/stany (np. tylko REQUIRED, tylko SHOW).
    validation: Sequence[Validator] = Field(default_factory=list)
    read_only: bool = False
    # Pole (liść) nie zawiera innych pól — COI zapisuje tu pustą listę.
    repeating_section_grid: Annotated[list[Any], Field(max_length=0)] = Field(
        default_factory=list, validation_alias=_GRID_ALIASES, serialization_alias=GRID_KEY
    )
    states: Sequence[State] = Field(default_factory=list)
    question_order: Annotated[int, Field(ge=0)] = 0
    tooltip_message: str = ""

    @field_validator("validation", mode="before")
    @classmethod
    def _empty_object_means_none(cls, value: Any) -> Any:
        # Eksport COI zawiera czasem "validation": {} zamiast [] — znaczy to samo.
        return [] if value == {} else value

    @field_validator("title", "description", "tooltip_message", mode="before")
    @classmethod
    def _null_text_is_empty(cls, value: Any) -> Any:
        return "" if value is None else value

    @model_validator(mode="after")
    def _options_only_for_choice(self) -> BaseField:
        if self.options and not self._choice:
            raise ValueError(f"pole typu {getattr(self, 'type', '?')} nie może mieć opcji")
        return self


class _ChoiceMixin(BaseField):
    """Opcje własne albo słownik — przy słowniku tablica opcji musi być pusta."""

    _choice: ClassVar[bool] = True
    dictionary_name: NonEmptyStr | None = None

    @model_validator(mode="after")
    def _options_xor_dictionary(self) -> _ChoiceMixin:
        if self.dictionary_name is not None and self.options:
            raise ValueError("pole korzystające ze słownika musi mieć pustą tablicę options")
        if self.dictionary_name is None and not self.options:
            raise ValueError("pole wyboru wymaga opcji albo słownika (dictionaryName)")
        return self


class _DefaultValueMixin(BaseField):
    default_value: DefaultValue | None = None
    default_value_type: DefaultValueType | None = None

    @model_validator(mode="after")
    def _external_requires_value(self) -> _DefaultValueMixin:
        if self.default_value_type is DefaultValueType.EXTERNAL_SYSTEM and not (
            isinstance(self.default_value, str) and self.default_value
        ):
            raise ValueError(
                "defaultValueType EXTERNAL_SYSTEM wymaga defaultValue 'ŹRÓDŁO.atrybut'"
            )
        return self


# ---------------------------------------------------------------- pola (liście)


class TextField(_DefaultValueMixin):
    type: Literal[FieldType.TEXT_FIELD]
    validation: list[Validator]


class CheckboxField(_ChoiceMixin):
    type: Literal[FieldType.CHECKBOX]
    validation: RequiredOnly


class NumberField(BaseField):
    type: Literal[FieldType.NUMBER]
    validation: RequiredOnly


class SelectField(_ChoiceMixin, _DefaultValueMixin):
    type: Literal[FieldType.SELECT]
    multi_level_tree: bool | None = None
    search_enabled: bool | None = None
    validation: RequiredOnly


class TextareaField(_DefaultValueMixin):
    type: Literal[FieldType.TEXTAREA]
    validation: RequiredOnly


class FileUploaderField(BaseField):
    type: Literal[FieldType.FILE_UPLOADER]
    default_value: str | None = None
    validation: RequiredOnly


class RadioField(_ChoiceMixin):
    type: Literal[FieldType.RADIO]
    validation: RequiredOnly


class DatepickerField(_DefaultValueMixin):
    """defaultValue dopuszczone, bo „Dane inicjalne” zasilają datepicker (PESEL.birthDate)."""

    type: Literal[FieldType.DATEPICKER]
    validation: RequiredOnly


class TextBlockField(BaseField):
    type: Literal[FieldType.TEXT_BLOCK]
    validation: Annotated[list[Validator], Field(max_length=0)] = Field(default_factory=list)
    states: list[ShowState] = Field(default_factory=list)


class ConsentSectionField(BaseField):
    type: Literal[FieldType.CONSENT_SECTION]
    select_all_enabled: bool | None = None
    consent_section_config: ConsentSectionConfig


class InfoSectionField(BaseField):
    type: Literal[FieldType.INFO_SECTION]
    description: NonEmptyStr
    validation: Annotated[list[Validator], Field(max_length=0)] = Field(default_factory=list)


LeafField = Annotated[
    TextField
    | CheckboxField
    | NumberField
    | SelectField
    | TextareaField
    | FileUploaderField
    | RadioField
    | DatepickerField
    | TextBlockField
    | ConsentSectionField
    | InfoSectionField,
    Field(discriminator="type"),
]


# ---------------------------------------------------------------- układ i sekcje


class _GridPosition(CoiModel):
    """Pozycja w siatce CSS: ``grid-area: {y+1} / {x+1} / span {rows} / span {cols}``.

    COI układa elementy w jednej kolumnie: ``x=0``, ``cols=3``; pole zajmuje 1 wiersz,
    sekcja — liczbę swoich pól + 1 (nagłówek).
    """

    x: Annotated[int, Field(ge=0)]
    y: Annotated[int, Field(ge=0)]
    rows: Annotated[int, Field(ge=1)]
    cols: Annotated[int, Field(ge=1)]


class FieldQuestion(_GridPosition):
    """Pole umieszczone w sekcji."""

    field: LeafField


class SectionField(BaseField):
    type: Literal[FieldType.SECTION, FieldType.REPEATING_SECTION]
    title: NonEmptyStr
    description: str = EMPTY_HTML
    validation: Annotated[list[Validator], Field(max_length=0)] = Field(default_factory=list)
    states: list[ShowState] = Field(default_factory=list)
    repeating_section_grid: list[FieldQuestion] = Field(
        default_factory=list, validation_alias=_GRID_ALIASES, serialization_alias=GRID_KEY
    )


class SectionQuestion(_GridPosition):
    """Sekcja umieszczona na stronie."""

    min_item_rows: Annotated[int, Field(ge=0)] | None = None
    field: SectionField


class Tab(CoiModel):
    id: UUID = Field(default_factory=uuid4)
    title: NonEmptyStr
    type: TabType
    questions: list[SectionQuestion]


class FlowLink(CoiModel):
    # Edytor COI nadaje krótkie identyfikatory przejść (np. "a16ec") — nie zawsze UUID.
    id: NonEmptyStr = Field(default_factory=lambda: str(uuid4()))
    source: UUID
    target: UUID


class Flow(CoiModel):
    links: list[FlowLink]


def _ordered(data: Any) -> Any:
    """Porządkuje klucze pól i pozycji jak w eksporcie COI (rekurencyjnie)."""
    if isinstance(data, list):
        return [_ordered(item) for item in data]
    if not isinstance(data, dict):
        return data
    order: tuple[str, ...] = ()
    if "techName" in data and "type" in data:
        order = FIELD_KEY_ORDER
    elif "field" in data and "x" in data:
        order = QUESTION_KEY_ORDER
    keys = [k for k in order if k in data] + [k for k in data if k not in order]
    return {k: _ordered(data[k]) for k in keys}


class FormDefinition(CoiModel):
    """Korzeń pliku importu COI."""

    title: Annotated[str, StringConstraints(min_length=1, max_length=MAX_FORM_TITLE_LENGTH)]
    tabs: list[Tab]
    flow: Flow

    def to_coi_json(self) -> dict[str, Any]:
        """Słownik gotowy do zapisu jako plik importu COI (kształt i kolejność kluczy jak w COI)."""
        result: dict[str, Any] = _ordered(
            self.model_dump(mode="json", by_alias=True, exclude_none=True)
        )
        return result

    def iter_fields(self) -> list[tuple[Tab, SectionField, LeafField | None]]:
        """Płaska lista (strona, sekcja, pole); sekcja bez pól daje wpis z polem None."""
        out: list[tuple[Tab, SectionField, LeafField | None]] = []
        for tab in self.tabs:
            for section_q in tab.questions:
                section = section_q.field
                if not section.repeating_section_grid:
                    out.append((tab, section, None))
                out.extend((tab, section, fq.field) for fq in section.repeating_section_grid)
        return out
