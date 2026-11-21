"""Modele konfiguracji e-formularza COI (``FormDefinition``).

Źródła (materiały organizatora):
- ``importowanyPlikJsonSchemat.json`` — schemat importu (nazwy i typy kluczy),
- „OpisImportowanegoPlikuJSON” — znaczenie kluczy,
- „Reguły techniczne konfiguracji formularzy i pól” — typy pól, dozwolone klucze per typ,
  walidatory, stany dynamiczne,
- „Dane inicjalne” — pola zasilane z rejestrów.

Decyzje przy niespójnościach materiałów (patrz docs/coi-specyfikacja.md):
- klucz pól w sekcji ma pisownię ze schematu: ``repetingSectionGrid``,
- ``defaultValue`` jest zawsze stringiem (jak w schemacie),
- dodatkowo modelujemy tylko klucze wprost wymienione w regułach technicznych:
  ``searchEnabled``, ``selectAllEnabled``, ``consents[].required``.

Reguły strukturalne wyrażone typami (nie da się ich złamać poprawnym obiektem):
- strona zawiera wyłącznie sekcje, a sekcja wyłącznie pola — bez zagnieżdżania sekcji,
- każdy typ pola przyjmuje tylko klucze dozwolone dla niego w regułach,
- pola wejściowe mają jawnie określoną obligatoryjność (klucz ``validation``).
Reguły dotyczące całego formularza (strony, przepływ, JSONata, unikalność) sprawdza
``backend.shared.validators.form_rules``.
"""

from enum import StrEnum
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from pydantic.alias_generators import to_camel

MAX_FORM_TITLE_LENGTH = 255

# Nazwa techniczna trafia do zapytań JSONata w apostrofach — bez spacji i cudzysłowów.
TechName = Annotated[str, StringConstraints(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$", max_length=100)]
NonEmptyStr = Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)]


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

# Pola wyboru poza TEXT_FIELD mogą mieć wyłącznie walidator wymagalności.
RequiredOnly = list[Literal[Validator.REQUIRED]]


class CoiModel(BaseModel):
    """Baza: klucze JSON w camelCase dokładnie jak w schemacie COI, nieznane klucze odrzucane."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
        use_enum_values=False,
        str_strip_whitespace=True,
    )


# ---------------------------------------------------------------- elementy wspólne


class Option(CoiModel):
    id: NonEmptyStr = Field(default_factory=lambda: str(uuid4()))
    name: NonEmptyStr
    tech_name: TechName


class State(CoiModel):
    """Stan dynamiczny: warunek JSONata, przy którym element jest pokazany / tylko do odczytu."""

    type: StateType
    query: NonEmptyStr


class SectionState(State):
    """Sekcje mają wyłącznie stan SHOW."""

    type: Literal[StateType.SHOW]


class ShowState(State):
    type: Literal[StateType.SHOW]


class Consent(CoiModel):
    description: NonEmptyStr
    required: bool | None = None


class ConsentSectionConfig(CoiModel):
    consents: Annotated[list[Consent], Field(min_length=1)]
    required: bool | None = None
    files: list[str] = Field(default_factory=list)


class BaseField(CoiModel):
    id: UUID = Field(default_factory=uuid4)
    tech_name: TechName
    question_order: Annotated[int, Field(ge=0)] | None = None
    tooltip_message: str | None = None


class _ChoiceMixin(CoiModel):
    """Opcje własne albo słownik — przy słowniku tablica opcji musi być pusta."""

    options: list[Option]
    dictionary_name: NonEmptyStr | None = None

    @model_validator(mode="after")
    def _options_xor_dictionary(self) -> _ChoiceMixin:
        if self.dictionary_name is not None and self.options:
            raise ValueError("pole korzystające ze słownika musi mieć pustą tablicę options")
        if self.dictionary_name is None and not self.options:
            raise ValueError("pole wyboru wymaga opcji albo słownika (dictionaryName)")
        return self


class _DefaultValueMixin(CoiModel):
    default_value: str | None = None
    default_value_type: DefaultValueType | None = None

    @model_validator(mode="after")
    def _external_requires_value(self) -> _DefaultValueMixin:
        if self.default_value_type is DefaultValueType.EXTERNAL_SYSTEM and not self.default_value:
            raise ValueError(
                "defaultValueType EXTERNAL_SYSTEM wymaga defaultValue 'ŹRÓDŁO.atrybut'"
            )
        return self


# ---------------------------------------------------------------- pola (liście)


class TextField(_DefaultValueMixin, BaseField):
    type: Literal[FieldType.TEXT_FIELD]
    title: str | None = None
    description: str | None = None
    read_only: bool | None = None
    validation: list[Validator]
    states: list[State] | None = None


class CheckboxField(_ChoiceMixin, BaseField):
    type: Literal[FieldType.CHECKBOX]
    title: str | None = None
    description: str | None = None
    read_only: bool | None = None
    validation: RequiredOnly
    states: list[State] | None = None


class NumberField(BaseField):
    type: Literal[FieldType.NUMBER]
    title: str | None = None
    description: str | None = None
    read_only: bool | None = None
    validation: RequiredOnly
    states: list[State] | None = None


class SelectField(_ChoiceMixin, _DefaultValueMixin, BaseField):
    type: Literal[FieldType.SELECT]
    title: str | None = None
    description: str | None = None
    read_only: bool | None = None
    search_enabled: bool | None = None
    validation: RequiredOnly
    states: list[State] | None = None


class TextareaField(_DefaultValueMixin, BaseField):
    type: Literal[FieldType.TEXTAREA]
    title: str | None = None
    description: str | None = None
    read_only: bool | None = None
    validation: RequiredOnly
    states: list[State] | None = None


class FileUploaderField(BaseField):
    type: Literal[FieldType.FILE_UPLOADER]
    title: str | None = None
    description: str | None = None
    default_value: str | None = None
    validation: RequiredOnly
    states: list[State] | None = None


class RadioField(_ChoiceMixin, BaseField):
    type: Literal[FieldType.RADIO]
    title: str | None = None
    description: str | None = None
    read_only: bool | None = None
    validation: RequiredOnly
    states: list[State] | None = None


class DatepickerField(_DefaultValueMixin, BaseField):
    """defaultValue dopuszczone, bo „Dane inicjalne” zasilają datepicker (PESEL.birthDate)."""

    type: Literal[FieldType.DATEPICKER]
    title: str | None = None
    description: str | None = None
    read_only: bool | None = None
    validation: RequiredOnly
    states: list[State] | None = None


class TextBlockField(BaseField):
    type: Literal[FieldType.TEXT_BLOCK]
    title: str | None = None
    description: str | None = None
    states: list[ShowState] | None = None


class ConsentSectionField(BaseField):
    type: Literal[FieldType.CONSENT_SECTION]
    select_all_enabled: bool | None = None
    consent_section_config: ConsentSectionConfig


class InfoSectionField(BaseField):
    type: Literal[FieldType.INFO_SECTION]
    description: NonEmptyStr


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
    """Pozycja w siatce CSS: ``grid-area: {y+1} / {x+1} / span {rows} / span {cols}``."""

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
    states: list[SectionState] | None = None
    # Pisownia jak w schemacie importu COI (sic!).
    repeting_section_grid: list[FieldQuestion] = Field(
        default_factory=list, alias="repetingSectionGrid"
    )


class SectionQuestion(_GridPosition):
    """Sekcja umieszczona na stronie."""

    field: SectionField


class Tab(CoiModel):
    id: UUID = Field(default_factory=uuid4)
    title: NonEmptyStr
    type: TabType
    questions: list[SectionQuestion]


class FlowLink(CoiModel):
    id: UUID = Field(default_factory=uuid4)
    source: UUID
    target: UUID


class Flow(CoiModel):
    links: list[FlowLink]


class FormDefinition(CoiModel):
    """Korzeń pliku importu COI."""

    title: Annotated[str, StringConstraints(min_length=1, max_length=MAX_FORM_TITLE_LENGTH)]
    tabs: list[Tab]
    flow: Flow

    def to_coi_json(self) -> dict[str, Any]:
        """Słownik gotowy do zapisu jako plik importu COI (camelCase, bez pustych kluczy)."""
        return self.model_dump(mode="json", by_alias=True, exclude_none=True)

    def iter_fields(self) -> list[tuple[Tab, SectionField, LeafField | None]]:
        """Płaska lista (strona, sekcja, pole); sekcja bez pól daje wpis z polem None."""
        out: list[tuple[Tab, SectionField, LeafField | None]] = []
        for tab in self.tabs:
            for section_q in tab.questions:
                section = section_q.field
                if not section.repeting_section_grid:
                    out.append((tab, section, None))
                out.extend((tab, section, fq.field) for fq in section.repeting_section_grid)
        return out
