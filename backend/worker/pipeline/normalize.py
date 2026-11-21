"""Normalizacja wyniku etapów 05–09 do pliku importu COI (``FormDefinition``).

Etapy 06–09 zwracają słownik zbliżony do schematu COI, ale z dodatkami, które w pliku
importu są niedozwolone. Normalizacja niczego nie gubi po cichu:

- metadane źródeł pól (``source_text``, ``page``, ``element_id``) → raport jakości,
- ``dictionary_proposals`` i ``conflicts`` z kroku 09 → raport jakości,
- pole leżące bezpośrednio na stronie (np. INFO_SECTION na FINISH) → opakowane w sekcję,
  bo według reguł COI każde pole musi być w sekcji,
- klucze niedozwolone dla typu pola (wg reguł technicznych) → usunięte, odnotowane w raporcie,
- ``defaultValueType: EXTERNAL_SYSTEM`` bez ``defaultValue`` → usunięte, ostrzeżenie
  (reguły wymagają wskazania ``ŹRÓDŁO.atrybut``; źródła nie zgadujemy),
- nazwy techniczne niezgodne z COI (np. zaczynające się cyfrą) → poprawione razem
  z odwołaniami w warunkach JSONata.

Wynik, który mimo to nie spełnia modeli COI, kończy etap błędem ``PipelineStageError``.
"""

import re
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel, ValidationError

from backend.shared.schemas.coi_form import (
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
)
from backend.shared.schemas.quality import (
    DictionaryEntry,
    DictionaryProposal,
    Issue,
    Severity,
)
from backend.worker.pipeline.errors import PipelineStageError

STAGE = "normalizacja do schematu COI"

SOURCE_KEYS = ("source_text", "page", "element_id")
_TECH_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

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


def _allowed_keys(model: type[BaseModel]) -> frozenset[str]:
    return frozenset(info.alias or name for name, info in model.model_fields.items())


ALLOWED_KEYS: dict[FieldType, frozenset[str]] = {t: _allowed_keys(m) for t, m in _MODELS.items()}


@dataclass(frozen=True, slots=True)
class StepSource:
    """Źródło pola zwrócone przez krok 07."""

    text: str | None
    page: int | None
    element_id: str | None


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
        cleaned = re.sub(r"[^A-Za-z0-9_]", "_", name).strip("_") or "x"
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
            state["query"] = query

    # ------------------------------------------------------------ pola

    def clean_field(self, field: dict[str, Any], path: str) -> None:
        uuid = _field_uuid(field)
        if uuid is not None and any(k in field for k in SOURCE_KEYS):
            page = field.get("page")
            self.sources[uuid] = StepSource(
                text=str(field["source_text"]) if field.get("source_text") else None,
                page=page if isinstance(page, int) and page >= 1 else None,
                element_id=str(field["element_id"]) if field.get("element_id") else None,
            )
        for key in SOURCE_KEYS:
            field.pop(key, None)

        try:
            field_type = FieldType(str(field.get("type")))
        except ValueError:
            return  # nieznany typ — zgłosi go walidacja modeli
        if field_type in SECTION_TYPES:
            self.fix_tech_name(field, path, prefix="sekcja")
        else:
            self.fix_tech_name(field, path)
            for option in field.get("options") or []:
                if isinstance(option, dict) and not _TECH_NAME_RE.match(
                    str(option.get("techName"))
                ):
                    old = str(option.get("techName") or "")
                    option["techName"] = f"opcja_{re.sub(r'[^A-Za-z0-9_]', '_', old).strip('_')}"

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
            "repetingSectionGrid": [
                {"x": 0, "y": 0, "rows": 1, "cols": question.get("cols", 1), "field": leaf}
            ],
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
            for q, question in enumerate(tab.get("questions") or []):
                if question.get("field", {}).get("type") not in {s.value for s in SECTION_TYPES}:
                    self.wrap_leaf(tab, q, question)
                section = question["field"]
                section_path = f"/tabs/{t}/questions/{q}/field"
                self.clean_field(section, section_path)
                for f, child in enumerate(section.get("repetingSectionGrid") or []):
                    self.clean_field(
                        child["field"], f"{section_path}/repetingSectionGrid/{f}/field"
                    )
        if self.renamed:
            for tab in form.get("tabs") or []:
                for question in tab.get("questions") or []:
                    self.rewrite_state_queries(question["field"])
                    for child in question["field"].get("repetingSectionGrid") or []:
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
