"""Porównanie wyniku pipeline'u z konfiguracją wyeksportowaną z COI (materiał referencyjny).

Uruchamia cały pipeline (ekstrakcja → etapy 05–09 → normalizacja → reguły → ocena pewności)
na pliku wniosku i porównuje wynik z referencyjnym JSON-em COI:

- strony i sekcje (dopasowanie po tytule),
- pola (dopasowanie po tytule/techName w obrębie formularza): precyzja, czułość, F1,
- zgodność typu, obligatoryjności (REQUIRED), słownika, danych inicjalnych i warunków (states)
  na dopasowanych polach,
- błędy reguł COI na wyniku.

Przykład (tryb demonstracyjny, bez modelu):

    python eval/compare_reference.py --demo \\
        --reference tests/fixtures/coi_reference/wniosek_numer_porzadkowy.json \\
        "Wniosek o ustalenie numeru porządkowego nieruchomości.pdf"

Z prawdziwym modelem: bez ``--demo``, z ``EDENAI_API_KEY`` w środowisku (koszt po stronie
konta Eden AI; ``report.usage`` w wyniku podaje tokeny i koszt).
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import sys
import unicodedata
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

GRID = "repeatingSectionGrid"
MATCH_THRESHOLD = 0.72


# ------------------------------------------------------------------ porównanie


def _norm(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text.replace("ł", "l").replace("Ł", "L"))
    ascii_text = "".join(ch for ch in folded if not unicodedata.combining(ch)).lower()
    return re.sub(r"[^a-z0-9]+", " ", ascii_text).strip()


def _similarity(a: dict[str, Any], b: dict[str, Any]) -> float:
    def ratio(x: str, y: str) -> float:
        return SequenceMatcher(None, _norm(x), _norm(y)).ratio() if x and y else 0.0

    return max(
        ratio(str(a.get("title") or ""), str(b.get("title") or "")),
        ratio(str(a.get("techName") or ""), str(b.get("techName") or "")),
    )


@dataclass
class FieldInfo:
    tab: str
    section: str
    field: dict[str, Any]

    @property
    def label(self) -> str:
        return f"{self.section}.{self.field.get('techName')}"


def _leaves(form: dict[str, Any]) -> list[FieldInfo]:
    return [
        FieldInfo(str(tab.get("title")), str(q["field"].get("techName")), child["field"])
        for tab in form.get("tabs", [])
        for q in tab.get("questions", [])
        for child in q["field"].get(GRID, [])
    ]


def _sections(form: dict[str, Any]) -> list[dict[str, Any]]:
    return [q["field"] for tab in form.get("tabs", []) for q in tab.get("questions", [])]


def _greedy_match(
    predicted: list[dict[str, Any]], expected: list[dict[str, Any]]
) -> list[tuple[int, int, float]]:
    """Pary (indeks przewidziany, indeks oczekiwany, podobieństwo) — każdy element raz."""
    candidates = sorted(
        (
            (_similarity(p, e), i, j)
            for i, p in enumerate(predicted)
            for j, e in enumerate(expected)
        ),
        reverse=True,
    )
    used_p: set[int] = set()
    used_e: set[int] = set()
    pairs: list[tuple[int, int, float]] = []
    for score, i, j in candidates:
        if score < MATCH_THRESHOLD:
            break
        if i in used_p or j in used_e:
            continue
        used_p.add(i)
        used_e.add(j)
        pairs.append((i, j, score))
    return pairs


def _prf(tp: int, predicted: int, expected: int) -> dict[str, float]:
    precision = tp / predicted if predicted else 0.0
    recall = tp / expected if expected else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"precision": round(precision, 3), "recall": round(recall, 3), "f1": round(f1, 3)}


def _required(f: dict[str, Any]) -> bool:
    validation = f.get("validation") or []
    return isinstance(validation, list) and "REQUIRED" in validation


@dataclass
class Comparison:
    tabs: dict[str, Any]
    sections: dict[str, Any]
    fields: dict[str, Any]
    attributes: dict[str, Any]
    matched: list[dict[str, Any]] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    extra: list[str] = field(default_factory=list)


def compare_forms(predicted: dict[str, Any], expected: dict[str, Any]) -> Comparison:
    """Metryki zgodności wyniku z konfiguracją referencyjną COI."""
    p_sections, e_sections = _sections(predicted), _sections(expected)
    section_pairs = _greedy_match(p_sections, e_sections)

    p_leaves, e_leaves = _leaves(predicted), _leaves(expected)
    pairs = _greedy_match([f.field for f in p_leaves], [f.field for f in e_leaves])

    agree: dict[str, list[bool]] = {
        "type": [], "required": [], "dictionary": [], "initial_data": [], "has_states": [],
    }  # fmt: skip
    matched: list[dict[str, Any]] = []
    for i, j, score in pairs:
        p, e = p_leaves[i].field, e_leaves[j].field
        agree["type"].append(p.get("type") == e.get("type"))
        agree["required"].append(_required(p) == _required(e))
        agree["dictionary"].append(p.get("dictionaryName") == e.get("dictionaryName"))
        agree["initial_data"].append(
            (p.get("defaultValue") if p.get("defaultValueType") else None)
            == (e.get("defaultValue") if e.get("defaultValueType") else None)
        )
        agree["has_states"].append(bool(p.get("states")) == bool(e.get("states")))
        matched.append(
            {
                "predicted": p_leaves[i].label,
                "expected": e_leaves[j].label,
                "similarity": round(score, 2),
                "type": f"{p.get('type')} / {e.get('type')}",
            }
        )

    matched_p = {i for i, _, _ in pairs}
    matched_e = {j for _, j, _ in pairs}
    return Comparison(
        tabs={
            "predicted": [t.get("title") for t in predicted.get("tabs", [])],
            "expected": [t.get("title") for t in expected.get("tabs", [])],
        },
        sections={
            "predicted": len(p_sections),
            "expected": len(e_sections),
            "matched": len(section_pairs),
            **_prf(len(section_pairs), len(p_sections), len(e_sections)),
        },
        fields={
            "predicted": len(p_leaves),
            "expected": len(e_leaves),
            "matched": len(pairs),
            **_prf(len(pairs), len(p_leaves), len(e_leaves)),
        },
        attributes={
            name: round(sum(values) / len(values), 3) if values else None
            for name, values in agree.items()
        },
        matched=matched,
        missing=[f.label for j, f in enumerate(e_leaves) if j not in matched_e],
        extra=[f.label for i, f in enumerate(p_leaves) if i not in matched_p],
    )


# ------------------------------------------------------------------ uruchomienie pipeline'u


async def run_document(path: Path, content: bytes, *, demo: bool) -> dict[str, Any]:
    """Cały pipeline na pliku; zwraca ``{"form", "report"}`` jak API."""
    from backend.api.file_validators import CANONICAL_MIME, EXTENSION_ALIASES
    from backend.shared.config import Settings
    from backend.shared.jobs import DocumentRef
    from backend.worker.document import document_workspace
    from backend.worker.pipeline.orchestrator import run_pipeline
    from backend.worker.progress import ProgressReporter

    class _NullProgress(ProgressReporter):
        def __init__(self) -> None:
            pass

        async def update(
            self, stage: str, step: int, total_steps: int, message: str | None = None
        ) -> None:
            print(f"  [{step}/{total_steps}] {stage}", file=sys.stderr)

    extension = path.suffix.lower().lstrip(".")
    extension = EXTENSION_ALIASES.get(extension, extension)
    if content.startswith(b"PK") and extension == "doc":
        extension = "docx"  # .doc z treścią DOCX — jak w API
    ref = DocumentRef(
        job_id="e" * 32,
        filename=path.name,
        extension=extension,
        mime_type=CANONICAL_MIME[extension],
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        owner="eval",
    )
    settings = Settings(llm_demo_mode=demo)
    with document_workspace(ref, content) as document:
        result = await run_pipeline(document, _NullProgress(), settings)
    return {"form": result.form.to_coi_json(), "report": result.report.model_dump(mode="json")}


def _print(name: str, comparison: Comparison, report: dict[str, Any]) -> None:
    print(f"\n=== {name}")
    print(f"strony  wynik:   {comparison.tabs['predicted']}")
    print(f"        wzorzec: {comparison.tabs['expected']}")
    s, f = comparison.sections, comparison.fields
    print(
        f"sekcje  {s['predicted']} / wzorzec {s['expected']}, dopasowane {s['matched']} "
        f"(P {s['precision']}, R {s['recall']}, F1 {s['f1']})"
    )
    print(
        f"pola    {f['predicted']} / wzorzec {f['expected']}, dopasowane {f['matched']} "
        f"(P {f['precision']}, R {f['recall']}, F1 {f['f1']})"
    )
    print("zgodność na dopasowanych:", comparison.attributes)
    errors = [i for i in report.get("issues", []) if i.get("severity") == "ERROR"]
    levels: dict[str, int] = {}
    for assessment in report.get("fields", []):
        levels[assessment["level"]] = levels.get(assessment["level"], 0) + 1
    print(f"błędy reguł COI: {len(errors)}; pewność pól: {levels}; usage: {report.get('usage')}")
    print("brakujące (wzorzec):", comparison.missing[:25])
    print("nadmiarowe (wynik):", comparison.extra[:25])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("documents", nargs="+", type=Path, help="pliki wniosku (dowolny format)")
    parser.add_argument("--reference", type=Path, required=True, help="JSON wyeksportowany z COI")
    parser.add_argument("--demo", action="store_true", help="bez modelu (LLM_DEMO_MODE)")
    parser.add_argument("--out", type=Path, help="katalog na wyniki (JSON) i metryki")
    args = parser.parse_args()

    expected = json.loads(args.reference.read_text(encoding="utf-8"))
    summary: dict[str, Any] = {}
    for path in args.documents:
        result = asyncio.run(run_document(path, path.read_bytes(), demo=args.demo))
        comparison = compare_forms(result["form"], expected)
        _print(path.name, comparison, result["report"])
        summary[path.name] = asdict(comparison)
        if args.out:
            args.out.mkdir(parents=True, exist_ok=True)
            stem = f"{path.stem}.{path.suffix.lstrip('.').lower()}"
            (args.out / f"{stem}.result.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
            )
    if args.out:
        (args.out / "metrics.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )


if __name__ == "__main__":
    main()
