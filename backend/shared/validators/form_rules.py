"""Walidacja semantyczna formularza COI — reguły, których nie da się wyrazić typami.

Źródło reguł: „Reguły techniczne konfiguracji formularzy i pól” (zadanie obligatoryjne)
i „Dane inicjalne” (zadanie rozszerzające). Funkcja nie rzuca wyjątków — zwraca listę
problemów z kodami, żeby pipeline mógł przekazać je do iteracji naprawczej, a interfejs
pokazać urzędnikowi.

Poziomy: ERROR = formularz łamie regułę COI; WARNING = prawdopodobny problem do sprawdzenia.
"""

import re
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from uuid import UUID

from backend.shared.schemas.coi_form import (
    INPUT_FIELD_TYPES,
    CheckboxField,
    DefaultValueType,
    FieldType,
    FormDefinition,
    LeafField,
    RadioField,
    SectionField,
    SelectField,
    State,
    TabType,
)
from backend.shared.schemas.initial_data import INITIAL_DATA_CATALOG
from backend.shared.schemas.quality import Issue, Severity

# Odwołanie JSONata do odpowiedzi pola — dwa warianty z materiału COI:
#   "X" in tabs['Strona' in title].questions['sekcja' in field.techName]
#          .field.repeatingSectionGrid['pole' in field.techName].field.__userAnswer
#   "X" in tabs['Strona' in title].questions['sekcja' in field.techName]
#          .field.__userAnswer[$repeatingSectionIndex]['pole' in fieldTechName].answers
# Akceptujemy obie pisownie klucza sekcji (schemat: repeting…, przykłady: repeating…).
_REF_RE = re.compile(
    r"""(?:"(?P<value>[^"]*)"\s*in\s*)?
        tabs\[\s*'(?P<tab>[^']*)'\s*in\s*title\s*\]
        \.questions\[\s*'(?P<section>[^']*)'\s*in\s*field\.techName\s*\]
        \.field\.
        (?:
            repea?tingSectionGrid\[\s*'(?P<field>[^']*)'\s*in\s*field\.techName\s*\]
          | __userAnswer\[\s*\$repeatingSectionIndex\s*\]
            \[\s*'(?P<rfield>[^']*)'\s*in\s*fieldTechName\s*\]
        )
    """,
    re.VERBOSE,
)

_CHOICE_TYPES = (CheckboxField, RadioField, SelectField)

# Klucz elementu: (tytuł strony, techName sekcji, techName pola lub None dla sekcji).
_ElementKey = tuple[str, str, str | None]


@dataclass(frozen=True, slots=True)
class StateReference:
    """Pole, na które wskazuje warunek JSONata, i ewentualna oczekiwana wartość."""

    tab: str
    section: str
    field: str
    value: str | None


def extract_references(query: str) -> list[StateReference]:
    """Wyciąga z zapytania JSONata odwołania do pól formularza."""
    return [
        StateReference(
            tab=m["tab"], section=m["section"], field=m["field"] or m["rfield"], value=m["value"]
        )
        for m in _REF_RE.finditer(query)
    ]


@dataclass(slots=True)
class _Element:
    key: _ElementKey
    path: str
    tab_index: int
    field_id: UUID
    states: list[State]
    leaf: LeafField | None = None
    section: SectionField | None = None
    depends_on: set[_ElementKey] = field(default_factory=set)


def validate_form_rules(form: FormDefinition) -> list[Issue]:
    issues: list[Issue] = []
    issues += _check_tabs(form)
    issues += _check_ids(form)
    issues += _check_tech_names_and_options(form)
    issues += _check_flow(form)
    issues += _check_fields(form)
    issues += _check_states(form)
    return issues


def has_errors(issues: Iterable[Issue]) -> bool:
    return any(i.severity is Severity.ERROR for i in issues)


# ------------------------------------------------------------------ pomocnicze


def _err(code: str, message: str, path: str = "", field_id: UUID | None = None) -> Issue:
    return Issue(code=code, severity=Severity.ERROR, message=message, path=path, field_id=field_id)


def _warn(code: str, message: str, path: str = "", field_id: UUID | None = None) -> Issue:
    return Issue(
        code=code, severity=Severity.WARNING, message=message, path=path, field_id=field_id
    )


def _section_path(t: int, s: int) -> str:
    return f"/tabs/{t}/questions/{s}/field"


def _field_path(t: int, s: int, f: int) -> str:
    return f"{_section_path(t, s)}/repetingSectionGrid/{f}/field"


# ------------------------------------------------------------------ strony


def _check_tabs(form: FormDefinition) -> list[Issue]:
    issues: list[Issue] = []
    if len(form.tabs) < 2:
        issues.append(_err("TABS_MIN_TWO", "Formularz musi mieć co najmniej dwie strony", "/tabs"))
    if form.tabs and form.tabs[-1].type is not TabType.FINISH:
        issues.append(
            _err(
                "FINISH_TAB_LAST",
                "Ostatnia strona musi być typu FINISH",
                f"/tabs/{len(form.tabs) - 1}/type",
            )
        )
    finish = [i for i, t in enumerate(form.tabs) if t.type is TabType.FINISH]
    if len(finish) > 1:
        issues.append(
            _err(
                "FINISH_TAB_SINGLE",
                f"Tylko jedna strona może być FINISH, jest {len(finish)}; pozostałe muszą być MAIN",
                "/tabs",
            )
        )

    for title, count in Counter(t.title for t in form.tabs).items():
        if count > 1:
            issues.append(
                _err(
                    "TAB_TITLE_DUPLICATE",
                    f"Tytuł strony „{title}” powtarza się (JSONata adresuje strony po tytule)",
                    "/tabs",
                )
            )

    for t, tab in enumerate(form.tabs):
        leaves = [fq.field for q in tab.questions for fq in q.field.repeting_section_grid]
        if not leaves:
            issues.append(
                _err(
                    "TAB_EMPTY",
                    f"Strona „{tab.title}” musi zawierać co najmniej jedno pole",
                    f"/tabs/{t}",
                )
            )
        for s, q in enumerate(tab.questions):
            if not q.field.repeting_section_grid:
                issues.append(
                    _warn(
                        "SECTION_EMPTY",
                        f"Sekcja „{q.field.title}” nie zawiera pól",
                        _section_path(t, s),
                        q.field.id,
                    )
                )
            if tab.type is TabType.FINISH:
                for f, fq in enumerate(q.field.repeting_section_grid):
                    if fq.field.type is not FieldType.INFO_SECTION:
                        issues.append(
                            _err(
                                "FINISH_ONLY_INFO_SECTION",
                                "Na stronie FINISH dozwolone są wyłącznie pola INFO_SECTION, "
                                f"a „{fq.field.tech_name}” ma typ {fq.field.type}",
                                _field_path(t, s, f),
                                fq.field.id,
                            )
                        )
        issues += _check_grid(
            [(q.x, q.y, q.rows, q.cols, q.field.question_order) for q in tab.questions],
            f"/tabs/{t}/questions",
        )
        for s, q in enumerate(tab.questions):
            issues += _check_grid(
                [
                    (fq.x, fq.y, fq.rows, fq.cols, fq.field.question_order)
                    for fq in q.field.repeting_section_grid
                ],
                f"{_section_path(t, s)}/repetingSectionGrid",
            )
    return issues


def _check_grid(cells: list[tuple[int, int, int, int, int | None]], path: str) -> list[Issue]:
    issues: list[Issue] = []
    occupied: dict[tuple[int, int], int] = {}
    for idx, (x, y, rows, cols, _) in enumerate(cells):
        for yy in range(y, y + rows):
            for xx in range(x, x + cols):
                other = occupied.setdefault((xx, yy), idx)
                if other != idx:
                    issues.append(
                        _warn(
                            "GRID_OVERLAP",
                            f"Elementy {other} i {idx} nachodzą na siebie (x={xx}, y={yy})",
                            path,
                        )
                    )
                    break
            else:
                continue
            break
    orders = [o for *_, o in cells if o is not None]
    if len(orders) != len(set(orders)):
        issues.append(_warn("QUESTION_ORDER_DUPLICATE", "Powtórzony questionOrder", path))
    return issues


# ------------------------------------------------------------------ identyfikatory


def _check_ids(form: FormDefinition) -> list[Issue]:
    ids: list[tuple[UUID, str]] = [(t.id, f"/tabs/{i}/id") for i, t in enumerate(form.tabs)]
    for t, tab in enumerate(form.tabs):
        for s, q in enumerate(tab.questions):
            ids.append((q.field.id, f"{_section_path(t, s)}/id"))
            ids += [
                (fq.field.id, f"{_field_path(t, s, f)}/id")
                for f, fq in enumerate(q.field.repeting_section_grid)
            ]
    ids += [(link.id, f"/flow/links/{i}/id") for i, link in enumerate(form.flow.links)]
    counts = Counter(i for i, _ in ids)
    return [
        _err("DUPLICATE_ID", f"Identyfikator {uid} występuje {counts[uid]} razy", path)
        for uid, path in ids
        if counts[uid] > 1
    ]


def _check_tech_names_and_options(form: FormDefinition) -> list[Issue]:
    issues: list[Issue] = []
    global_names: Counter[str] = Counter()
    for t, tab in enumerate(form.tabs):
        section_names = Counter(q.field.tech_name for q in tab.questions)
        for s, q in enumerate(tab.questions):
            section = q.field
            global_names[section.tech_name] += 1
            if section_names[section.tech_name] > 1:
                issues.append(
                    _err(
                        "TECHNAME_DUPLICATE_IN_TAB",
                        f"techName sekcji „{section.tech_name}” powtarza się na stronie "
                        f"„{tab.title}”",
                        _section_path(t, s),
                        section.id,
                    )
                )
            field_names = Counter(fq.field.tech_name for fq in section.repeting_section_grid)
            for f, fq in enumerate(section.repeting_section_grid):
                leaf = fq.field
                global_names[leaf.tech_name] += 1
                path = _field_path(t, s, f)
                if field_names[leaf.tech_name] > 1:
                    issues.append(
                        _err(
                            "TECHNAME_DUPLICATE_IN_SECTION",
                            f"techName pola „{leaf.tech_name}” powtarza się w sekcji "
                            f"„{section.tech_name}”",
                            path,
                            leaf.id,
                        )
                    )
                if isinstance(leaf, _CHOICE_TYPES):
                    for attr, code in (
                        ("name", "OPTION_NAME_DUPLICATE"),
                        ("tech_name", "OPTION_TECHNAME_DUPLICATE"),
                        ("id", "OPTION_ID_DUPLICATE"),
                    ):
                        counts = Counter(getattr(o, attr) for o in leaf.options)
                        issues += [
                            _err(
                                code,
                                f"Opcja {attr}={value!r} powtarza się",
                                f"{path}/options",
                                leaf.id,
                            )
                            for value, n in counts.items()
                            if n > 1
                        ]
    issues += [
        _warn("TECHNAME_DUPLICATE", f"techName „{name}” występuje {n} razy w formularzu")
        for name, n in global_names.items()
        if n > 1
    ]
    return issues


# ------------------------------------------------------------------ przepływ stron


def _check_flow(form: FormDefinition) -> list[Issue]:
    issues: list[Issue] = []
    tab_ids = {t.id for t in form.tabs}
    graph: dict[UUID, set[UUID]] = defaultdict(set)
    seen_pairs: set[tuple[UUID, UUID]] = set()

    for i, link in enumerate(form.flow.links):
        path = f"/flow/links/{i}"
        if link.source not in tab_ids or link.target not in tab_ids:
            issues.append(_err("FLOW_UNKNOWN_TAB", "Przejście wskazuje nieistniejącą stronę", path))
            continue
        if link.source == link.target:
            issues.append(_err("FLOW_SELF_LINK", "Przejście ze strony do niej samej", path))
            continue
        if (link.source, link.target) in seen_pairs:
            issues.append(_warn("FLOW_DUPLICATE_LINK", "Powtórzone przejście", path))
        seen_pairs.add((link.source, link.target))
        graph[link.source].add(link.target)

    if len(form.tabs) < 2:
        return issues

    finish_ids = {t.id for t in form.tabs if t.type is TabType.FINISH}
    reachable = _reachable(form.tabs[0].id, graph)
    reverse: dict[UUID, set[UUID]] = defaultdict(set)
    for src, targets in graph.items():
        for dst in targets:
            reverse[dst].add(src)
    can_finish: set[UUID] = set()
    for fid in finish_ids:
        can_finish |= _reachable(fid, reverse)

    for t, tab in enumerate(form.tabs):
        if tab.id not in reachable:
            issues.append(
                _err(
                    "FLOW_UNREACHABLE_TAB",
                    f"Strona „{tab.title}” jest nieosiągalna z pierwszej strony",
                    f"/tabs/{t}",
                )
            )
        if tab.type is TabType.FINISH and graph.get(tab.id):
            issues.append(
                _err("FLOW_FINISH_OUTGOING", "Strona FINISH nie może prowadzić dalej", f"/tabs/{t}")
            )
        if tab.type is TabType.MAIN and tab.id not in can_finish:
            issues.append(
                _err(
                    "FLOW_NO_PATH_TO_FINISH",
                    f"Ze strony „{tab.title}” nie da się dojść do strony FINISH",
                    f"/tabs/{t}",
                )
            )
    return issues


def _reachable(start: UUID, graph: dict[UUID, set[UUID]]) -> set[UUID]:
    seen = {start}
    stack = [start]
    while stack:
        for nxt in graph.get(stack.pop(), ()):
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return seen


# ------------------------------------------------------------------ pola


def _check_fields(form: FormDefinition) -> list[Issue]:
    issues: list[Issue] = []
    for t, tab in enumerate(form.tabs):
        for s, q in enumerate(tab.questions):
            for f, fq in enumerate(q.field.repeting_section_grid):
                leaf = fq.field
                path = _field_path(t, s, f)
                if leaf.type in INPUT_FIELD_TYPES and not getattr(leaf, "title", None):
                    issues.append(
                        _warn(
                            "INPUT_WITHOUT_TITLE",
                            "Pole do wypełnienia nie ma etykiety",
                            path,
                            leaf.id,
                        )
                    )
                issues += _check_initial_data(leaf, path)
    return issues


def _check_initial_data(leaf: LeafField, path: str) -> list[Issue]:
    default_value = getattr(leaf, "default_value", None)
    default_type = getattr(leaf, "default_value_type", None)
    if default_type is not DefaultValueType.EXTERNAL_SYSTEM:
        if isinstance(default_value, str) and default_value in INITIAL_DATA_CATALOG:
            return [
                _warn(
                    "INITIAL_DATA_MISSING_TYPE",
                    f"defaultValue „{default_value}” wygląda na dane inicjalne, ale brak "
                    "defaultValueType EXTERNAL_SYSTEM",
                    path,
                    leaf.id,
                )
            ]
        return []

    issues: list[Issue] = []
    entry = INITIAL_DATA_CATALOG.get(default_value or "")
    if entry is None:
        issues.append(
            _err(
                "INITIAL_DATA_UNKNOWN",
                f"Nieznane źródło/atrybut danych inicjalnych „{default_value}”",
                f"{path}/defaultValue",
                leaf.id,
            )
        )
    elif entry.field_type is not None and entry.field_type is not leaf.type:
        issues.append(
            _warn(
                "INITIAL_DATA_TYPE_MISMATCH",
                f"„{entry.reference}” powinno zasilać pole {entry.field_type}, a pole ma typ "
                f"{leaf.type}",
                path,
                leaf.id,
            )
        )
    if getattr(leaf, "read_only", None) is not True:
        issues.append(
            _err(
                "INITIAL_DATA_NOT_READONLY",
                "Pole zasilane danymi inicjalnymi musi mieć readOnly: true",
                f"{path}/readOnly",
                leaf.id,
            )
        )
    return issues


# ------------------------------------------------------------------ stany (JSONata)


def _index_elements(form: FormDefinition) -> dict[_ElementKey, _Element]:
    index: dict[_ElementKey, _Element] = {}
    for t, tab in enumerate(form.tabs):
        for s, q in enumerate(tab.questions):
            section = q.field
            skey: _ElementKey = (tab.title, section.tech_name, None)
            index.setdefault(
                skey,
                _Element(
                    skey,
                    _section_path(t, s),
                    t,
                    section.id,
                    list(section.states or []),
                    section=section,
                ),
            )
            for f, fq in enumerate(section.repeting_section_grid):
                leaf = fq.field
                fkey: _ElementKey = (tab.title, section.tech_name, leaf.tech_name)
                index.setdefault(
                    fkey,
                    _Element(
                        fkey,
                        _field_path(t, s, f),
                        t,
                        leaf.id,
                        list(getattr(leaf, "states", None) or []),
                        leaf=leaf,
                    ),
                )
    return index


def _check_states(form: FormDefinition) -> list[Issue]:
    issues: list[Issue] = []
    index = _index_elements(form)
    tab_titles = {t.title for t in form.tabs}
    section_keys = {(k[0], k[1]) for k in index}

    for element in index.values():
        for i, state in enumerate(element.states):
            path = f"{element.path}/states/{i}/query"
            refs = extract_references(state.query)
            if not refs:
                issues.append(
                    _warn(
                        "STATE_QUERY_UNPARSED",
                        "Nie rozpoznano odwołania do pola w warunku JSONata — sprawdź ręcznie",
                        path,
                        element.field_id,
                    )
                )
            for ref in refs:
                target_key: _ElementKey = (ref.tab, ref.section, ref.field)
                if ref.tab not in tab_titles:
                    issues.append(
                        _err(
                            "STATE_UNKNOWN_TAB",
                            f"Warunek odwołuje się do nieistniejącej strony „{ref.tab}”",
                            path,
                            element.field_id,
                        )
                    )
                    continue
                if (ref.tab, ref.section) not in section_keys:
                    issues.append(
                        _err(
                            "STATE_UNKNOWN_SECTION",
                            f"Warunek odwołuje się do nieistniejącej sekcji "
                            f"„{ref.section}” na stronie „{ref.tab}”",
                            path,
                            element.field_id,
                        )
                    )
                    continue
                target = index.get(target_key)
                if target is None:
                    issues.append(
                        _err(
                            "STATE_UNKNOWN_FIELD",
                            f"Warunek odwołuje się do nieistniejącego pola "
                            f"„{ref.field}” w sekcji „{ref.section}”",
                            path,
                            element.field_id,
                        )
                    )
                    continue
                if target_key == element.key or (
                    element.leaf is None and target_key[:2] == element.key[:2]
                ):
                    issues.append(
                        _err(
                            "STATE_SELF_REFERENCE",
                            "Element nie może zależeć od samego siebie ani od pola "
                            "we własnej sekcji (dla stanu sekcji)",
                            path,
                            element.field_id,
                        )
                    )
                    continue
                if target.tab_index > element.tab_index:
                    issues.append(
                        _warn(
                            "STATE_FORWARD_REFERENCE",
                            "Warunek zależy od pola z późniejszej strony",
                            path,
                            element.field_id,
                        )
                    )
                issues += _check_ref_value(ref, target, path, element.field_id)
                element.depends_on.add(target_key)

    issues += _check_cycles(index)
    return issues


def _check_ref_value(
    ref: StateReference, target: _Element, path: str, field_id: UUID
) -> list[Issue]:
    leaf = target.leaf
    if ref.value is None or not isinstance(leaf, _CHOICE_TYPES) or leaf.dictionary_name:
        return []
    names = {o.name for o in leaf.options}
    if ref.value in names:
        return []
    return [
        _err(
            "STATE_UNKNOWN_OPTION",
            f"Wartość „{ref.value}” nie jest opcją pola „{leaf.tech_name}” "
            f"(dostępne: {', '.join(sorted(names))})",
            path,
            field_id,
        )
    ]


def _check_cycles(index: dict[_ElementKey, _Element]) -> list[Issue]:
    white, grey, black = 0, 1, 2
    color: dict[_ElementKey, int] = dict.fromkeys(index, white)
    issues: list[Issue] = []

    def visit(key: _ElementKey, trail: list[_ElementKey]) -> None:
        color[key] = grey
        for nxt in index[key].depends_on:
            if color.get(nxt) == grey:
                cycle = [*trail[trail.index(nxt) :], nxt] if nxt in trail else [key, nxt]
                names = " → ".join(k[2] or k[1] for k in cycle)
                issues.append(
                    _err(
                        "STATE_CYCLE",
                        f"Cykl zależności warunków: {names}",
                        index[key].path,
                        index[key].field_id,
                    )
                )
            elif color.get(nxt) == white:
                visit(nxt, [*trail, nxt])
        color[key] = black

    for key in index:
        if color[key] == white:
            visit(key, [key])
    return issues
