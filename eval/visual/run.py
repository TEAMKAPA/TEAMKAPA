"""Ewaluacja detekcji elementów wizualnych na zbiorze #12.

Użycie::

    python -m eval.visual.run --system opencv --predictions <katalog>
    python -m eval.visual.run --system opencv --predictions <katalog> \
        --baseline eval/visual/baseline.json

Predykcje: plik ``<file_id>-p<NN>.json`` (``PagePredictions``) dla każdej ocenianej strony pliku.
Domyślnie liczona jest wyłącznie część ``dev``. Część ``holdout`` wymaga ``--split holdout
--final``, a każde takie uruchomienie jest dopisywane do ``holdout_runs.log``. Przy ``dev``
skrypt nie otwiera etykiet ani predykcji ``holdout``.

Kod wyjścia: 0 (bez regresji), 1 (regresja względem bazy), 2 (błąd danych lub wywołania).
"""

import argparse
import json
import re
import sys
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from eval.visual.match import MatchParams, from_label, from_prediction
from eval.visual.metrics import PageCase, PageEval, Summary, evaluate_page, macro_over, summarize
from eval.visual.models import (
    LABELS_SCHEMA,
    Manifest,
    ManifestDocument,
    ManifestFile,
    ManifestPage,
    ManifestVariant,
    PageLabels,
    PagePredictions,
    Split,
    System,
    predictions_file_name,
    read_json,
    write_text,
)
from eval.visual.prepare import PrepareError, git_state
from eval.visual.report import LIMITATIONS, REPORT_SCHEMA, markdown

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = REPO_ROOT / "dane" / "robocze" / "b" / "manifest.full.json"
DEFAULT_LABELS = REPO_ROOT / "dane" / "robocze" / "labels"
DEFAULT_OUT = REPO_ROOT / "dane" / "robocze" / "eval"
HOLDOUT_LOG = "holdout_runs.log"
REPORTED_IOU = (0.3, 0.5, 0.7)
BASELINE_SCHEMA = "teamkapa.vision.baseline/1"
PREDICTION_FILE = re.compile(
    r"^(?P<file_id>[a-z]+[0-9]{2,}(?:-[a-z0-9]+)+)-p(?P<page>[0-9]{2,})\.json$"
)


class EvalError(RuntimeError):
    """Niekompletne lub niespójne dane ewaluacji (nic nie jest pomijane po cichu)."""


@dataclass(frozen=True)
class LabeledPage:
    document: ManifestDocument
    page: ManifestPage
    labels: PageLabels


@dataclass(frozen=True)
class Dataset:
    split: Split
    labeled: tuple[LabeledPage, ...]
    unlabeled: tuple[str, ...]  # label_id bez pliku etykiet

    @property
    def label_sets(self) -> int:
        return len(self.labeled) + len(self.unlabeled)


def format_group(file: ManifestFile, variant: ManifestVariant) -> str:
    """PDF, obraz będący wariantem strony PDF albo obraz z własnymi etykietami (skany COI)."""
    if file.format == "pdf":
        return "pdf"
    return "image_variant" if variant.check is not None else "image_own"


def load_dataset(manifest: Manifest, labels_dir: Path, split: Split) -> Dataset:
    """Etykiety wyłącznie dokumentów części ``split`` (innych plików etykiet nie otwiera)."""
    labeled: list[LabeledPage] = []
    unlabeled: list[str] = []
    for document in manifest.documents:
        if document.split is not split:
            continue
        for page in document.pages:
            path = labels_dir / f"{page.label_id}.json"
            if not path.is_file():
                unlabeled.append(page.label_id)
                continue
            labels = read_json(path, PageLabels)
            if labels.schema_version != LABELS_SCHEMA or labels.label_id != page.label_id:
                raise EvalError(f"{path.name}: plik etykiet nie należy do strony {page.label_id}")
            if labels.source.image_sha256 != page.image_sha256:
                raise EvalError(f"{page.label_id}: etykiety dotyczą innego obrazu niż manifest")
            labeled.append(LabeledPage(document=document, page=page, labels=labels))
    return Dataset(split=split, labeled=tuple(labeled), unlabeled=tuple(unlabeled))


def _known_pages(manifest: Manifest) -> set[tuple[str, int]]:
    return {
        (f.file_id, number)
        for d in manifest.documents
        for f in d.files
        if f.page_count is not None
        for number in range(1, f.page_count + 1)
    }


def load_cases(
    dataset: Dataset, manifest: Manifest, predictions: Path, system: System
) -> tuple[list[PageCase], int]:
    """Strony oceny z predykcjami; zwraca też liczbę plików predykcji spoza oceny (pomijanych)."""
    if not predictions.is_dir():
        raise EvalError(f"brak katalogu predykcji {predictions}")
    known = _known_pages(manifest)
    present: set[tuple[str, int]] = set()
    for path in predictions.glob("*.json"):
        match = PREDICTION_FILE.match(path.name)
        if match is None:
            raise EvalError(f"{path.name}: nazwa pliku predykcji spoza wzorca <file_id>-pNN.json")
        key = (match.group("file_id"), int(match.group("page")))
        if key not in known:
            raise EvalError(f"{path.name}: strona nie istnieje w manifeście")
        present.add(key)

    cases: list[PageCase] = []
    missing: list[str] = []
    for item in dataset.labeled:
        files = {f.file_id: f for f in item.document.files}
        gt = tuple(from_label(e) for e in item.labels.elements)
        for variant in item.page.variants:
            if not variant.included:
                continue
            name = predictions_file_name(variant.file_id, variant.page)
            if (variant.file_id, variant.page) not in present:
                missing.append(name)
                continue
            prediction = read_json(predictions / name, PagePredictions)
            if (prediction.file_id, prediction.page) != (variant.file_id, variant.page):
                raise EvalError(f"{name}: file_id/page w pliku niezgodne z nazwą")
            if prediction.system is not system:
                raise EvalError(
                    f"{name}: system {prediction.system.value}, oczekiwano {system.value}"
                )
            if (prediction.width_px, prediction.height_px) != (
                item.page.width_px,
                item.page.height_px,
            ):
                raise EvalError(f"{name}: rozmiar strony różni się od manifestu")
            cases.append(
                PageCase(
                    label_id=item.page.label_id,
                    doc_id=item.document.doc_id,
                    file_id=variant.file_id,
                    page=variant.page,
                    format_group=format_group(files[variant.file_id], variant),
                    width_px=item.page.width_px,
                    height_px=item.page.height_px,
                    gt=gt,
                    pred=tuple(from_prediction(e) for e in prediction.elements),
                )
            )
    if missing:
        raise EvalError("brak predykcji dla stron: " + ", ".join(sorted(missing)))
    return cases, len(present) - len(cases)


def _slices(pages: list[PageEval], key: str) -> dict[str, Summary]:
    groups: dict[str, list[PageEval]] = defaultdict(list)
    for page in pages:
        groups[getattr(page.case, key)].append(page)
    return {name: summarize(groups[name]) for name in sorted(groups)}


def build_report(
    cases: Sequence[PageCase],
    dataset: Dataset,
    system: System,
    params: MatchParams,
    commit: tuple[str, bool],
    skipped_predictions: int = 0,
) -> dict[str, Any]:
    pages = [evaluate_page(case, params) for case in cases]
    by_document = _slices(pages, "doc_id")
    micro_by_iou = {
        str(threshold): summarize(
            evaluate_page(case, replace(params, iou=threshold)) for case in cases
        )
        .micro()
        .as_dict()["f1"]
        for threshold in REPORTED_IOU
    }
    return {
        "schema_version": REPORT_SCHEMA,
        "created": datetime.now().isoformat(timespec="seconds"),
        "commit": commit[0],
        "dirty": commit[1],
        "system": system.value,
        "split": dataset.split.value,
        "params": asdict(params),
        "coverage": {
            "label_sets": dataset.label_sets,
            "labeled": len(dataset.labeled),
            "unlabeled": list(dataset.unlabeled),
            "preliminary": bool(dataset.unlabeled),
            "evaluation_pages": len(cases),
            "skipped_prediction_files": skipped_predictions,
        },
        "overall": summarize(pages).as_dict(),
        "micro_f1_by_iou": micro_by_iou,
        "by_format": {k: v.as_dict() for k, v in _slices(pages, "format_group").items()},
        "by_document": {k: v.as_dict() for k, v in by_document.items()},
        "macro_f1_documents": macro_over(by_document),
        "limitations": list(LIMITATIONS),
    }


# --- Baza (baseline) ---


def baseline_from(report: dict[str, Any], tolerance: float) -> dict[str, Any]:
    overall = report["overall"]
    return {
        "schema_version": BASELINE_SCHEMA,
        "system": report["system"],
        "split": report["split"],
        "iou": report["params"]["iou"],
        "tolerance": tolerance,
        "commit": report["commit"],
        "metrics": {
            "micro_f1": overall["micro"]["f1"],
            "micro_recall": overall["micro"]["recall"],
            "micro_precision": overall["micro"]["precision"],
            "per_kind_f1": {k: v["f1"] for k, v in overall["per_kind"].items()},
        },
    }


def compare(report: dict[str, Any], baseline: dict[str, Any]) -> list[str]:
    """Metryki niższe od bazy o więcej niż tolerancja (brak metryki w raporcie to też regresja)."""
    if baseline.get("schema_version") != BASELINE_SCHEMA:
        raise EvalError("plik bazy ma nieznany format")
    for key in ("system", "split"):
        if baseline[key] != report[key]:
            raise EvalError(f"baza dotyczy {key}={baseline[key]}, raport {key}={report[key]}")
    if baseline["iou"] != report["params"]["iou"]:
        raise EvalError("baza policzona przy innym progu IoU")
    tolerance = float(baseline["tolerance"])
    current = baseline_from(report, tolerance)["metrics"]
    regressions = []
    expected: dict[str, Any] = baseline["metrics"]
    flat_expected = {k: v for k, v in expected.items() if k != "per_kind_f1"}
    flat_expected |= {f"f1[{k}]": v for k, v in expected.get("per_kind_f1", {}).items()}
    flat_current = {k: v for k, v in current.items() if k != "per_kind_f1"}
    flat_current |= {f"f1[{k}]": v for k, v in current["per_kind_f1"].items()}
    for name, value in sorted(flat_expected.items()):
        if value is None:
            continue
        now = flat_current.get(name)
        if now is None or now < value - tolerance:
            regressions.append(f"{name}: {now} < baza {value} - {tolerance}")
    return regressions


# --- CLI ---


def _log_holdout(out: Path, commit: str, system: System, predictions: Path, iou: float) -> None:
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().isoformat(timespec="seconds")
    line = f"{stamp}\t{commit}\t{system.value}\t{predictions}\tiou={iou}\n"
    with (out / HOLDOUT_LOG).open("a", encoding="utf-8", newline="\n") as log:
        log.write(line)


def run(args: argparse.Namespace) -> int:
    split = Split(args.split)
    if split is Split.HOLDOUT and not args.final:
        raise EvalError("holdout tylko z --final (raport końcowy; uruchomienie jest odnotowywane)")
    if split is Split.DEV and args.final:
        raise EvalError("--final dotyczy wyłącznie holdout")
    system = System(args.system)
    params = MatchParams(iou=args.iou)
    commit = git_state(REPO_ROOT)
    manifest = read_json(args.manifest, Manifest)
    if split is Split.HOLDOUT:
        _log_holdout(args.out, commit[0], system, args.predictions, args.iou)
    dataset = load_dataset(manifest, args.labels, split)
    if not dataset.labeled:
        raise EvalError(f"brak oznaczonych stron w części {split.value}")
    cases, skipped = load_cases(dataset, manifest, args.predictions, system)
    report = build_report(cases, dataset, system, params, commit, skipped)
    status = 0
    if args.baseline is not None:
        baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
        report["regressions"] = compare(report, baseline)
        status = 1 if report["regressions"] else 0
    stem = f"{split.value}-{system.value}"
    write_text(args.out / f"{stem}.json", json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    write_text(args.out / f"{stem}.md", markdown(report))
    if args.write_baseline is not None:
        if report["coverage"]["preliminary"]:
            raise EvalError("baza wymaga pełnego pokrycia części (wszystkie strony oznaczone)")
        write_text(
            args.write_baseline,
            json.dumps(baseline_from(report, args.tolerance), ensure_ascii=False, indent=2) + "\n",
        )
    micro = report["overall"]["micro"]
    print(
        f"{system.value} / {split.value}: {len(cases)} stron, P={micro['precision']}"
        f" R={micro['recall']} F1={micro['f1']}"
        + (" (wstępny pomiar: niepełne pokrycie)" if report["coverage"]["preliminary"] else "")
    )
    for regression in report.get("regressions", []):
        print(f"REGRESJA: {regression}")
    print(f"Raport: {args.out / (stem + '.md')}")
    return status


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Ewaluacja detekcji elementów wizualnych (#12)")
    p.add_argument("--system", required=True, choices=[s.value for s in System])
    p.add_argument("--predictions", type=Path, required=True)
    p.add_argument("--split", default=Split.DEV.value, choices=[s.value for s in Split])
    p.add_argument("--final", action="store_true", help="wymagane dla holdout")
    p.add_argument("--iou", type=float, default=0.5)
    p.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    p.add_argument("--labels", type=Path, default=DEFAULT_LABELS)
    p.add_argument("--out", type=Path, default=DEFAULT_OUT)
    p.add_argument("--baseline", type=Path, default=None)
    p.add_argument("--write-baseline", type=Path, default=None)
    p.add_argument("--tolerance", type=float, default=0.02)
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        return run(args)
    except (EvalError, PrepareError, ValueError, OSError) as error:
        print(f"BŁĄD: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
