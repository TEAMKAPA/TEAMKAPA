"""Normalizacja wyniku etapów 05–09 do pliku importu COI (``FormDefinition``).

Etapy 06–09 zwracają słownik zbliżony do formatu COI, ale z dodatkami, które w pliku
importu są niedozwolone. Normalizacja niczego nie gubi po cichu:

- metadane źródeł pól (``source_text``, ``page``, ``element_id``) i znacznik ``origin``
  (pole ze standardowego szablonu COI) → raport jakości,
- ``dictionary_proposals`` i ``conflicts`` z kroku 09 → raport jakości,
- pole leżące bezpośrednio na stronie (np. blok tekstu na FINISH) → opakowane w sekcję,
  bo według reguł COI każde pole musi być w sekcji,
- klucze, opcje i walidatory niedozwolone dla typu pola → usunięte, odnotowane w raporcie,
- ``defaultValueType: EXTERNAL_SYSTEM`` bez ``defaultValue`` → usunięte, ostrzeżenie
  (reguły wymagają wskazania ``ŹRÓDŁO.atrybut``; źródła nie zgadujemy),
- nazwy techniczne niezgodne z COI (np. zaczynające się cyfrą) → poprawione razem
  z odwołaniami w warunkach JSONata.

Kształt wyniku jak w plikach eksportowanych przez COI (``tests/fixtures/coi_reference``):
- układ w jednej kolumnie: ``x=0``, ``cols=3``; pole zajmuje 1 wiersz, sekcja — liczbę pól
  + 1; ``y`` i ``questionOrder`` rosną od 0 (kolejność z etapów zostaje zachowana),
- opisy sekcji, bloków tekstu, pól tekstowych wielowierszowych i zgód w HTML,
- identyfikator opcji = jej nazwa, opcja ma ``techName``,
- komplet kluczy wspólnych w każdym polu (uzupełnia model ``FormDefinition``).

Wynik, który mimo to nie spełnia modeli COI, kończy etap błędem ``PipelineStageError``.
"""

import html
import re
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel, ValidationError
from pydantic.alias_generators import to_camel

from backend.shared.schemas.coi_form import (
    EMPTY_HTML,
    GRID_KEY,
    SECTION_TYPES,
    CheckboxField,
    ConsentSectionField,
    DatepickerField,
    FieldType,
    FileUploaderField,
    FormDefinition,
    InfoSectionField,
    NumberField,
    RadioField,
    SectionField,
    SelectField,
    TextareaField,
    TextBlockField,
    TextField,
    Validator,
)
from backend.shared.schemas.quality import (
    DictionaryEntry,
    DictionaryProposal,
    Issue,
    Severity,
)
from backend.worker.pipeline.errors import PipelineStageError
from backend.worker.pipeline.step06_structure import slugify_tech_name

STAGE = "normalizacja do schematu COI"

SOURCE_KEYS = ("source_text", "page", "element_id")
ORIGIN_KEY = "origin"
_TECH_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_LEGACY_GRID_KEY = "repetingSectionGrid"  # pisownia ze schematu importu

#: Układ COI: jedna kolumna o szerokości 3.
GRID_COLS = 3

_MODELS: dict[FieldType, type[BaseModel]] = {
    FieldType.SECTION: SectionField,
    FieldType.REPEATING_SECTION: SectionField,
    FieldType.TEXT_FIELD: TextField,
    FieldType.CHECKBOX: CheckboxField,
    FieldType.NUMBER: NumberField,
    FieldType.SELECT: SelectField,
    FieldType.TEXTAREA: TextareaField,
    FieldType.FILE_UPLOADER: FileUploaderField,
    FieldType.RADIO: RadioField,
    FieldType.DATEPICKER: DatepickerField,
    FieldType.TEXT_BLOCK: TextBlockField,
    FieldType.CONSENT_SECTION: ConsentSectionField,
    FieldType.INFO_SECTION: InfoSectionField,
}

_CHOICE_TYPES = frozenset({FieldType.CHECKBOX, FieldType.RADIO, FieldType.SELECT})
#: Typy bez walidatorów (sekcje, informacje, zgody — obligatoryjność zgód jest w consents).
_NO_VALIDATION = frozenset(
    {*SECTION_TYPES, FieldType.TEXT_BLOCK, FieldType.INFO_SECTION, FieldType.CONSENT_SECTION}
)
#: Typy, których opis COI zapisuje jako dokument HTML (treść widoczna na formularzu).
_HTML_DESCRIPTION = frozenset(
    {*SECTION_TYPES, FieldType.TEXT_BLOCK, FieldType.TEXTAREA, FieldType.INFO_SECTION}
)


def _allowed_keys(model: type[BaseModel]) -> frozenset[str]:
    return frozenset(
        info.serialization_alias or info.alias or to_camel(name)
        for name, info in model.model_fields.items()
    )


ALLOWED_KEYS: dict[FieldType, frozenset[str]] = {t: _allowed_keys(m) for t, m in _MODELS.items()}


@dataclass(frozen=True, slots=True)
class StepSource:
    """Źródło pola zwrócone przez krok 07."""

    text: str | None
    page: int | None
    element_id: str | None
    #: "standard" — pole ze standardowego szablonu COI (nie pochodzi z dokumentu).
    origin: str | None = None


@dataclass(slots=True)
class NormalizedForm:
    form: FormDefinition
    sources: dict[UUID, StepSource] = field(default_factory=dict)
    issues: list[Issue] = field(default_factory=list)
    proposals: list[DictionaryProposal] = field(default_factory=list)
    conflicts: dict[UUID, list[dict[str, Any]]] = field(default_factory=dict)


def _is_empty(value: Any) -> bool:
    return value is None or value == [] or value == {} or value == "" or value is False


def _field_uuid(raw: dict[str, Any]) -> UUID | None:
    try:
        return UUID(str(raw.get("id")))
    except ValueError:
        return None


def to_html(text: str) -> str:
    """Tekst → dokument HTML jak w edytorze COI (akapity z pustych linii, wiersze z ``<br>``).

    Tekst, który już jest HTML-em, zostaje bez zmian (opakowany w dokument, jeśli trzeba).
    """
    stripped = text.strip()
    if not stripped:
        return EMPTY_HTML
    if stripped.lower().startswith("<html"):
        return stripped
    if re.match(r"^<(p|div|ul|ol|strong|a|span|h\d)\b", stripped, re.IGNORECASE):
        body = stripped
    else:
        paragraphs = [p for p in re.split(r"\n\s*\n", stripped) if p.strip()]
        body = "".join(
            "<p>" + "<br>".join(html.escape(line.strip()) for line in p.splitlines()) + "</p>"
            for p in paragraphs
        )
    return f"<html><head></head><body>{body}</body></html>"


def _grid(section: dict[str, Any]) -> list[dict[str, Any]]:
    """Lista pól sekcji pod kluczem COI (pisownia ze schematu jest przenoszona)."""
    if _LEGACY_GRID_KEY in section:
        legacy = section.pop(_LEGACY_GRID_KEY) or []
        section[GRID_KEY] = [*(section.get(GRID_KEY) or []), *legacy]
    grid = section.get(GRID_KEY)
    if not isinstance(grid, list):
        grid = section[GRID_KEY] = []
    return grid


def _position_key(index: int, item: dict[str, Any]) -> tuple[int, int, int, int]:
    def number(key: str, source: dict[str, Any]) -> int:
        value = source.get(key)
        return value if isinstance(value, int) else 10**6

    field_ = item.get("field") or {}
    return (number("y", item), number("x", item), number("questionOrder", field_), index)


def _layout(items: list[dict[str, Any]], rows_of: Any) -> None:
    """Układ COI: elementy jeden pod drugim w kolejności z etapów (y, x, questionOrder)."""
    ordered = [item for _, item in sorted(enumerate(items), key=lambda p: _position_key(*p))]
    items[:] = ordered
    y = 0
    for item in items:
        rows = rows_of(item)
        item.update(x=0, y=y, rows=rows, cols=GRID_COLS)
        item["field"]["questionOrder"] = y
        y += rows


class _Normalizer:
    def __init__(self, raw: dict[str, Any]) -> None:
        self.raw = deepcopy(raw)
        self.issues: list[Issue] = []
        self.sources: dict[UUID, StepSource] = {}
        self.renamed: dict[str, str] = {}
        self.tab_index = 0

    def note(
        self, code: str, message: str, path: str, field: dict[str, Any], *, warn: bool = False
    ) -> None:
        self.issues.append(
            Issue(
                code=code,
                severity=Severity.WARNING if warn else Severity.INFO,
                message=message,
                path=path,
                field_id=_field_uuid(field),
            )
        )

    # ------------------------------------------------------------ nazwy techniczne

    def fix_tech_name(self, field: dict[str, Any], path: str, prefix: str = "pole") -> None:
        name = str(field.get("techName") or "")
        if _TECH_NAME_RE.match(name):
            return
        cleaned = slugify_tech_name(name or str(field.get("title") or ""), fallback="x")
        new = cleaned if _TECH_NAME_RE.match(cleaned) else f"{prefix}_{cleaned}"
        field["techName"] = new
        if name:
            self.renamed[name] = new
        self.note(
            "TECHNAME_NORMALIZED",
            f"Nazwa techniczna „{name}” niezgodna z COI zmieniona na „{new}”",
            f"{path}/techName",
            field,
        )

    def rewrite_state_queries(self, field: dict[str, Any]) -> None:
        for state in field.get("states") or []:
            query = str(state.get("query", ""))
            for old, new in self.renamed.items():
                for template in ("'{}' in field.techName", "'{}' in fieldTechName"):
                    query = query.replace(template.format(old), template.format(new))
            state["query"] = query.replace(_LEGACY_GRID_KEY, GRID_KEY)

    # ------------------------------------------------------------ pola

    def clean_options(self, field: dict[str, Any]) -> None:
        for option in field.get("options") or []:
            if not isinstance(option, dict):
                continue
            name = str(option.get("name") or "").strip()
            if name:
                option["id"] = name  # konwencja COI: identyfikator opcji = nazwa
            tech = str(option.get("techName") or "")
            if not _TECH_NAME_RE.match(tech):
                slug = slugify_tech_name(tech or name, fallback="opcja")
                option["techName"] = slug if _TECH_NAME_RE.match(slug) else f"opcja_{slug}"

    def clean_validation(self, field: dict[str, Any], field_type: FieldType, path: str) -> None:
        raw = field.get("validation")
        if raw is None or raw == {}:
            field["validation"] = []
            return
        values = raw if isinstance(raw, list) else [raw]
        if field_type in _NO_VALIDATION:
            allowed: set[str] = set()
        elif field_type is FieldType.TEXT_FIELD:
            allowed = {v.value for v in Validator}
        else:
            allowed = {Validator.REQUIRED.value}
        kept = list(dict.fromkeys(str(v) for v in values if str(v) in allowed))
        dropped = [str(v) for v in values if str(v) not in allowed]
        field["validation"] = kept
        if dropped:
            self.note(
                "VALIDATOR_NOT_ALLOWED_REMOVED",
                f"Usunięto walidatory niedozwolone dla typu {field_type}: {', '.join(dropped)}",
                f"{path}/validation",
                field,
                warn=True,
            )

    def clean_field(self, field: dict[str, Any], path: str) -> None:
        uuid = _field_uuid(field)
        origin = field.pop(ORIGIN_KEY, None)
        if uuid is not None and (origin or any(k in field for k in SOURCE_KEYS)):
            page = field.get("page")
            self.sources[uuid] = StepSource(
                text=str(field["source_text"]) if field.get("source_text") else None,
                page=page if isinstance(page, int) and page >= 1 else None,
                element_id=str(field["element_id"]) if field.get("element_id") else None,
                origin=str(origin) if origin else None,
            )
        for key in SOURCE_KEYS:
            field.pop(key, None)

        try:
            field_type = FieldType(str(field.get("type")))
        except ValueError:
            return  # nieznany typ — zgłosi go walidacja modeli
        if field_type in SECTION_TYPES:
            self.fix_tech_name(field, path, prefix="sekcja")
            _grid(field)
        else:
            self.fix_tech_name(field, path)
            if _is_empty(field.get(GRID_KEY)) and _is_empty(field.get(_LEGACY_GRID_KEY)):
                field.pop(_LEGACY_GRID_KEY, None)

        if (
            field_type in _CHOICE_TYPES
            and not field.get("options")
            and not field.get("dictionaryName")
        ):
            # Pole wyboru bez opcji i słownika nie przejdzie importu — zostaje polem tekstowym.
            field["type"] = FieldType.TEXT_FIELD.value
            self.note(
                "CHOICE_WITHOUT_OPTIONS",
                f"Pole wyboru „{field.get('title') or field.get('techName')}” nie ma opcji ani "
                "słownika — zamienione na pole tekstowe; uzupełnij opcje ręcznie",
                path,
                field,
                warn=True,
            )
            field_type = FieldType.TEXT_FIELD
        if field_type in _CHOICE_TYPES:
            self.clean_options(field)
            if field.get("dictionaryName") and field.get("options"):
                field["options"] = []  # słownik ma pierwszeństwo przed opcjami
        elif field.get("options"):
            self.note(
                "KEY_NOT_ALLOWED_REMOVED",
                f"Usunięto opcje z pola typu {field_type} (opcje mają tylko pola wyboru)",
                path,
                field,
            )
            field["options"] = []
        self.clean_validation(field, field_type, path)

        if (
            field_type is FieldType.SELECT
            and isinstance(field.get("defaultValue"), str)
            and field.get("defaultValueType") != "EXTERNAL_SYSTEM"
        ):
            # Wybór ze słownika COI zapisuje jako listę (np. ["Polska"]).
            field["defaultValue"] = [field["defaultValue"]] if field["defaultValue"] else []

        if field.get("defaultValueType") == "EXTERNAL_SYSTEM" and not field.get("defaultValue"):
            field.pop("defaultValueType")
            self.note(
                "INITIAL_DATA_SOURCE_MISSING",
                f"Pole „{field.get('title') or field.get('techName')}” oznaczono jako dane "
                "inicjalne bez wskazania źródła i atrybutu (np. MLPZ.name) — uzupełnij ręcznie",
                path,
                field,
                warn=True,
            )

        if field_type in _HTML_DESCRIPTION:
            field["description"] = to_html(str(field.get("description") or ""))
        elif field.get("description"):
            field["description"] = to_html(str(field["description"]))
        config = field.get("consentSectionConfig")
        if isinstance(config, dict):
            for consent in config.get("consents") or []:
                if isinstance(consent, dict) and consent.get("description"):
                    consent["description"] = to_html(str(consent["description"]))

        allowed = ALLOWED_KEYS[field_type]
        removed = sorted(k for k in field if k not in allowed and not _is_empty(field[k]))
        for key in [k for k in field if k not in allowed]:
            field.pop(key)
        if removed:
            self.note(
                "KEY_NOT_ALLOWED_REMOVED",
                f"Usunięto klucze niedozwolone dla typu {field_type}: {', '.join(removed)}",
                path,
                field,
            )

    # ------------------------------------------------------------ struktura

    def wrap_leaf(self, tab: dict[str, Any], index: int, question: dict[str, Any]) -> None:
        leaf = question["field"]
        title = str(leaf.get("title") or tab.get("title") or "Informacje")
        leaf_id = str(leaf.get("id") or f"{tab.get('id')}:{index}")
        question["field"] = {
            "id": str(uuid5(NAMESPACE_URL, f"teamkapa:section-wrapper:{leaf_id}")),
            "type": "SECTION",
            "techName": f"{leaf.get('techName') or 'pole'}_sekcja",
            "title": title,
            GRID_KEY: [{"x": 0, "y": 0, "rows": 1, "cols": GRID_COLS, "field": leaf}],
        }
        self.note(
            "WRAPPED_IN_SECTION",
            f"Pole {leaf.get('type')} „{title}” leżało bezpośrednio na stronie — umieszczono je "
            "w sekcji (reguła COI: każde pole w sekcji)",
            f"/tabs/{self.tab_index}/questions/{index}/field",
            leaf,
        )

    def run(self) -> NormalizedForm:
        proposals = self._proposals(self.raw.pop("dictionary_proposals", None) or [])
        raw_conflicts = self.raw.pop("conflicts", None) or []
        form = {k: self.raw[k] for k in ("title", "tabs", "flow") if k in self.raw}

        for t, tab in enumerate(form.get("tabs") or []):
            self.tab_index = t
            tab.pop(ORIGIN_KEY, None)
            questions = tab.get("questions") or []
            for q, question in enumerate(questions):
                if question.get("field", {}).get("type") not in {s.value for s in SECTION_TYPES}:
                    self.wrap_leaf(tab, q, question)
                section = question["field"]
                section_path = f"/tabs/{t}/questions/{q}/field"
                self.clean_field(section, section_path)
                children = _grid(section)
                for f, child in enumerate(children):
                    self.clean_field(child["field"], f"{section_path}/{GRID_KEY}/{f}/field")
                _layout(children, lambda _: 1)
            _layout(questions, lambda q: len(q["field"].get(GRID_KEY) or []) + 1)

        for tab in form.get("tabs") or []:
            for question in tab.get("questions") or []:
                self.rewrite_state_queries(question["field"])
                for child in question["field"].get(GRID_KEY) or []:
                    self.rewrite_state_queries(child["field"])

        try:
            definition = FormDefinition.model_validate(form)
        except ValidationError as exc:
            first = exc.errors()[0]
            location = "/".join(str(p) for p in first["loc"])
            raise PipelineStageError(
                STAGE,
                f"wynik niezgodny ze schematem COI ({exc.error_count()} błędów, "
                f"np. {location}: {first['msg']})",
            ) from exc

        return NormalizedForm(
            form=definition,
            sources=self.sources,
            issues=self.issues,
            proposals=proposals,
            conflicts=self._conflicts_by_field(definition, raw_conflicts),
        )

    def _proposals(self, raw: list[Any]) -> list[DictionaryProposal]:
        proposals: list[DictionaryProposal] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            entries = (item.get("structure") or {}).get("entries") or [
                {"code": v, "label": v} for v in item.get("values") or []
            ]
            tech = str(item.get("field_id") or "")
            proposals.append(
                DictionaryProposal(
                    tech_name=self.renamed.get(tech, tech),
                    name=str(item.get("name") or tech),
                    entries=[
                        DictionaryEntry(code=str(e.get("code", "")), label=str(e.get("label", "")))
                        for e in entries
                        if isinstance(e, dict)
                    ],
                    usage=str(item["usage"]) if item.get("usage") else None,
                )
            )
        return proposals

    def _conflicts_by_field(
        self, definition: FormDefinition, raw: list[Any]
    ) -> dict[UUID, list[dict[str, Any]]]:
        by_tech: dict[str, UUID] = {}
        for _, _, leaf in definition.iter_fields():
            if leaf is not None:
                by_tech.setdefault(leaf.tech_name, leaf.id)
        result: dict[UUID, list[dict[str, Any]]] = {}
        for conflict in raw:
            if not isinstance(conflict, dict):
                continue
            tech = str(conflict.get("field_id") or "")
            field_id = by_tech.get(self.renamed.get(tech, tech))
            if field_id is not None:
                result.setdefault(field_id, []).append(conflict)
        return result


def normalize_form(raw: dict[str, Any]) -> NormalizedForm:
    """Zamienia wynik etapów 05–09 na ``FormDefinition`` + informacje do raportu jakości."""
    return _Normalizer(raw).run()
