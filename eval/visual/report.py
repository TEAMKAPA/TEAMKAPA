"""Raport ewaluacji detekcji (#12): JSON (pełny) i Markdown (do czytania).

Pełne raporty zawierają identyfikatory stron i liczności per dokument, więc zapisujemy je
w ``dane/robocze/eval/``. Do repozytorium (``docs/vision.md``) trafiają wyłącznie liczby zbiorcze.
"""

from typing import Any

from backend.worker.vision.models import ElementKind

REPORT_SCHEMA = "teamkapa.vision.eval/1"

FORMAT_NAMES = {
    "pdf": "PDF",
    "image_variant": "obraz (wariant strony PDF)",
    "image_own": "obraz z własnymi etykietami (w COI: skany)",
}

LIMITATIONS = (
    "Jeden oznaczający: brak zgodności między oznaczającymi; szum etykiet szacuje kontrola "
    "ciasności ramek i (później) ponowne oznaczenie stron.",
    "Warianty formatu jednej strony nie są niezależnymi próbkami: wyniki per format i dokument.",
    "IoU cienkich linii liczone na ramkach rozszerzonych pionowo do minimalnej wysokości.",
    "Elementy z flagą uncertain są ignorowane jak 'difficult' w PASCAL VOC (liczone osobno).",
    "Pokrycie zbioru: liczone są tylko zestawy etykiet z plikiem etykiet; przy niepełnym pokryciu "
    "wynik jest wyłącznie wstępnym pomiarem.",
)


def _pct(value: float | None) -> str:
    return "–" if value is None else f"{value:.3f}"


def _ci(pair: list[float] | None) -> str:
    return "–" if pair is None else f"{pair[0]:.2f}–{pair[1]:.2f}"


def _kind_table(summary: dict[str, Any]) -> list[str]:
    lines = [
        "| Kategoria | TP | FP | FN | ign. | P | P 95% | R | R 95% | F1 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    rows = [
        (k.value, summary["per_kind"][k.value])
        for k in ElementKind
        if k.value in summary["per_kind"]
    ]
    rows.append(("**mikro**", summary["micro"]))
    for name, c in rows:
        lines.append(
            f"| {name} | {c['tp']} | {c['fp']} | {c['fn']} | {c['ignored']}"
            f" | {_pct(c['precision'])} | {_ci(c['precision_ci95'])}"
            f" | {_pct(c['recall'])} | {_ci(c['recall_ci95'])}"
            f" | {_pct(c['f1'])} |"
        )
    lines.append("")
    lines.append(f"Makro-F1 (kategorie): {_pct(summary['macro_f1'])}.")
    return lines


def _slices(title: str, slices: dict[str, Any], names: dict[str, str] | None = None) -> list[str]:
    lines = [
        f"### {title}",
        "",
        "| Przekrój | Strony | TP | FP | FN | P | R | F1 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for key, summary in slices.items():
        c = summary["micro"]
        name = (names or {}).get(key, key)
        lines.append(
            f"| {name} | {summary['pages']} | {c['tp']} | {c['fp']} | {c['fn']}"
            f" | {_pct(c['precision'])} | {_pct(c['recall'])} | {_pct(c['f1'])} |"
        )
    return lines


def _matrix(title: str, nested: dict[str, dict[str, int]]) -> list[str]:
    if not nested:
        return []
    columns = sorted({c for row in nested.values() for c in row})
    lines = [f"### {title}", "", "| etykieta \\ predykcja | " + " | ".join(columns) + " |"]
    lines.append("|---" * (len(columns) + 1) + "|")
    for row, values in nested.items():
        lines.append(f"| {row} | " + " | ".join(str(values.get(c, "")) for c in columns) + " |")
    return lines


def markdown(report: dict[str, Any]) -> str:
    overall = report["overall"]
    coverage = report["coverage"]
    lines = [
        f"# Ewaluacja detekcji: {report['system']} na `{report['split']}`",
        "",
        f"Commit `{report['commit'][:12]}`"
        f"{' (zmiany niezacommitowane)' if report['dirty'] else ''},"
        f" IoU ≥ {report['params']['iou']}, {overall['pages']} stron oceny.",
        f"Pokrycie: {coverage['labeled']} z {coverage['label_sets']} zestawów etykiet części"
        f" `{report['split']}`."
        + (" **Wstępny pomiar (niepełne pokrycie).**" if coverage["preliminary"] else ""),
        "",
        "## Całość",
        "",
        *_kind_table(overall),
        "",
        "Mikro-F1 przy innych progach IoU: "
        + ", ".join(f"{k}: {_pct(v)}" for k, v in report["micro_f1_by_iou"].items())
        + ".",
        "",
        f"Kratki: {overall['cells']['n']} par, zgodność {_pct(overall['cells']['exact_rate'])},"
        f" MAE {_pct(overall['cells']['mae'])}. Role: {overall['roles']['n']} par, trafność"
        f" {_pct(overall['roles']['accuracy'])}. Grupy: {overall['multi']['n']}, trafność multi"
        f" {_pct(overall['multi']['accuracy'])}. Etykiet trudnych (uncertain):"
        f" {overall['difficult_gt']}.",
        "",
        *_slices("Per format", report["by_format"], FORMAT_NAMES),
        "",
        *_slices("Per dokument", report["by_document"]),
        "",
        f"Makro-F1 po dokumentach: {_pct(report['macro_f1_documents'])}.",
        "",
        *_matrix("Macierz pomyłek kategorii", overall["kind_confusion"]),
        "",
        *_matrix("Macierz pomyłek ról (dopasowane)", overall["roles"]["confusion"]),
        "",
        "## Znane ograniczenia",
        "",
        *[f"- {item}" for item in report["limitations"]],
    ]
    if report.get("regressions"):
        lines += ["", "## Regresje względem bazy", "", *[f"- {r}" for r in report["regressions"]]]
    return "\n".join(lines) + "\n"
