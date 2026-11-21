"""Leave-one-out evaluation script for Step 09 deterministic rules and COI dicts.

Designed to run offline in CI without external API keys.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.worker.pipeline.step09_rules_dicts import run_step09  # noqa: E402


@dataclass
class FoldMetrics:
    """Evaluation metrics for a single leave-one-out fold."""

    fold: int
    form_id: str
    train_size: int
    expected_fields: int
    predicted_fields: int
    tp: int
    fp: int
    fn: int
    precision: float
    recall: float
    f1: float
    field_type_accuracy: float
    conflicts_resolved: int
    proposals_matched: int
    proposals_expected: int
    estimated_cost_usd: float


def load_dataset(dataset_dir: Path) -> list[tuple[str, dict[str, Any], dict[str, Any]]]:
    """Load sorted (form_id, input_data, expected_data) pairs from dataset_dir."""
    pairs: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    for input_path in sorted(dataset_dir.glob("*_input.json")):
        stem = input_path.name.removesuffix("_input.json")
        expected_path = dataset_dir / f"{stem}_expected.json"
        if not expected_path.exists():
            raise FileNotFoundError(f"Missing expected file for {input_path.name}")

        with input_path.open(encoding="utf-8") as fh:
            input_data = json.load(fh)
        with expected_path.open(encoding="utf-8") as fh:
            expected_data = json.load(fh)

        form_id = str(expected_data.get("form_id") or stem)
        pairs.append((form_id, input_data, expected_data))
    return pairs


def _field_signature(field: dict[str, Any]) -> tuple[str, Any, Any, Any]:
    return (
        str(field.get("id", "")),
        field.get("type"),
        field.get("validator"),
        field.get("dictionary"),
    )


def evaluate_prediction(predicted: dict[str, Any], expected: dict[str, Any]) -> dict[str, Any]:
    """Compare predicted form output against expected ground truth."""
    pred_fields = predicted.get("fields", [])
    exp_fields = expected.get("fields", [])

    pred_by_id = {str(f["id"]): f for f in pred_fields if "id" in f}
    exp_by_id = {str(f["id"]): f for f in exp_fields if "id" in f}

    pred_sigs = {_field_signature(f) for f in pred_fields}
    exp_sigs = {_field_signature(f) for f in exp_fields}

    tp = len(pred_sigs & exp_sigs)
    fp = len(pred_sigs - exp_sigs)
    fn = len(exp_sigs - pred_sigs)

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    denom = precision + recall
    f1 = (2 * precision * recall / denom) if denom > 0 else 0.0

    correct_types = sum(
        1
        for field_id, exp_field in exp_by_id.items()
        if field_id in pred_by_id and pred_by_id[field_id].get("type") == exp_field.get("type")
    )
    field_type_accuracy = correct_types / len(exp_by_id) if exp_by_id else 0.0

    expected_proposals = set(expected.get("expected_dictionary_proposals", []))
    raw_proposals = predicted.get("dictionary_proposals", [])
    predicted_proposals = {str(p.get("name", "")) for p in raw_proposals}
    proposals_matched = len(expected_proposals & predicted_proposals)

    return {
        "expected_fields": len(exp_fields),
        "predicted_fields": len(pred_fields),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "field_type_accuracy": field_type_accuracy,
        "conflicts_resolved": len(predicted.get("conflicts", [])),
        "proposals_matched": proposals_matched,
        "proposals_expected": len(expected_proposals),
    }


def run_leave_one_out(dataset_dir: Path) -> dict[str, Any]:
    """Evaluate local deterministic rules in leave-one-out mode."""
    samples = load_dataset(dataset_dir)
    if not samples:
        raise ValueError(f"No *_input.json pairs found in {dataset_dir}")

    folds: list[FoldMetrics] = []
    for idx, (form_id, input_data, expected_data) in enumerate(samples):
        train_samples = [s for j, s in enumerate(samples) if j != idx]
        predicted = run_step09(input_data)
        metrics = evaluate_prediction(predicted, expected_data)

        folds.append(
            FoldMetrics(
                fold=idx + 1,
                form_id=form_id,
                train_size=len(train_samples),
                estimated_cost_usd=0.0,
                **metrics,
            )
        )

    n = len(folds)
    summary = {
        "mode": "leave-one-out",
        "num_folds": n,
        "precision": sum(f.precision for f in folds) / n,
        "recall": sum(f.recall for f in folds) / n,
        "f1": sum(f.f1 for f in folds) / n,
        "field_type_accuracy": sum(f.field_type_accuracy for f in folds) / n,
        "total_conflicts_resolved": sum(f.conflicts_resolved for f in folds),
        "estimated_cost_usd": sum(f.estimated_cost_usd for f in folds),
    }
    return {
        "summary": summary,
        "folds": [asdict(f) for f in folds],
    }


def format_report(report: dict[str, Any]) -> str:
    """Format evaluation results as a readable CLI table."""
    lines = [
        "=== COI Form Rules & Dictionaries Evaluation (Leave-One-Out) ===",
        "",
        f"{'Fold':<6}{'Form ID':<26}{'Prec':>8}{'Rec':>8}{'F1':>8}"
        f"{'TypeAcc':>10}{'Confl':>7}{'Props':>8}{'Cost($)':>9}",
        "-" * 90,
    ]
    for fold in report["folds"]:
        props = f"{fold['proposals_matched']}/{fold['proposals_expected']}"
        lines.append(
            f"{fold['fold']:<6}{fold['form_id']:<26}"
            f"{fold['precision']:>8.3f}{fold['recall']:>8.3f}{fold['f1']:>8.3f}"
            f"{fold['field_type_accuracy']:>10.3f}{fold['conflicts_resolved']:>7d}"
            f"{props:>8}{fold['estimated_cost_usd']:>9.4f}"
        )

    summary = report["summary"]
    lines.extend(
        [
            "-" * 90,
            f"Folds:               {summary['num_folds']} (leave-one-out)",
            f"Macro Precision:     {summary['precision']:.4f}",
            f"Macro Recall:        {summary['recall']:.4f}",
            f"Macro F1:            {summary['f1']:.4f}",
            f"Field Type Accuracy: {summary['field_type_accuracy']:.4f}",
            f"Conflicts Resolved:  {summary['total_conflicts_resolved']}",
            f"Estimated API Cost:  ${summary['estimated_cost_usd']:.4f} (local rules)",
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run offline leave-one-out evaluation for Step 09 rules & dicts."
    )
    parser.add_argument(
        "--dataset-dir",
        type=Path,
        default=REPO_ROOT / "eval" / "dataset",
        help="Path to directory containing *_input.json and *_expected.json pairs.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON instead of a text report.",
    )
    parser.add_argument(
        "--min-f1",
        type=float,
        default=0.0,
        help="Fail with exit code 1 if macro F1 is below this threshold.",
    )
    args = parser.parse_args(argv)

    report = run_leave_one_out(args.dataset_dir)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(format_report(report))

    if report["summary"]["f1"] < args.min_f1:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
