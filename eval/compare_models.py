"""Compare Eden AI open-weight LLM extraction metrics across COI benchmark forms.

Evaluates Qwen3, Llama 3.3, Gemma 3, and Mistral Small via the Eden AI API client.
Runs offline in mock mode by default so it works in CI without API keys.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.worker.clients.llm_client import (  # noqa: E402
    DEFAULT_EDENAI_BASE_URL,
    LLMClient,
    LLMConfig,
)
from backend.worker.pipeline.step05_doc_type import run_step05  # noqa: E402
from backend.worker.pipeline.step06_structure import run_step06  # noqa: E402
from backend.worker.pipeline.step07_fields import run_step07  # noqa: E402
from backend.worker.pipeline.step08_dependencies import (  # noqa: E402
    run_step08,
    validate_form_definition,
)

DEFAULT_MODELS: tuple[str, ...] = (
    "scaleway/qwen3-235b-a22b-instruct-2507",
    "scaleway/llama-3.3-70b-instruct",
    "scaleway/gemma-3-27b-it",
    "mistral/mistral-small-latest",
)

BENCHMARK_FORMS: list[dict[str, Any]] = [
    {
        "case_id": "dowod_osobisty",
        "expected_doc_type": "wniosek_dowod_osobisty",
        "document": {
            "title": "Wniosek o wydanie dowodu osobistego (DO-01)",
            "elements": [
                {"element_id": "e1", "page": 1, "text": "Kod formularza: DO-01"},
                {"element_id": "e2", "page": 1, "text": "Urząd Gminy"},
                {"element_id": "e3", "page": 1, "text": "Imię i nazwisko, PESEL"},
                {
                    "element_id": "e4",
                    "page": 1,
                    "label": "Powód złożenia wniosku",
                    "text": "Powód złożenia wniosku: Pierwszy dowód / Zmiana danych",
                    "options": ["Pierwszy dowód", "Zmiana danych", "Utrata dowodu"],
                    "required": True,
                },
                {
                    "element_id": "e5",
                    "page": 1,
                    "label": "Fotografia wnioskodawcy",
                    "text": "Załącznik: Fotografia wnioskodawcy (JPG)",
                    "required": True,
                },
                {
                    "element_id": "e6",
                    "page": 2,
                    "label": "Dodatkowe uwagi",
                    "text": "Dodatkowe uwagi do wniosku (opcjonalnie)",
                    "required": False,
                },
                {
                    "element_id": "e7",
                    "page": 2,
                    "text": "Oświadczam, że podane dane są zgodne z prawdą.",
                },
            ],
        },
        "expected_fields": {
            ("nazwa_urzedu", "TEXT_FIELD"),
            ("imie", "TEXT_FIELD"),
            ("nazwisko", "TEXT_FIELD"),
            ("pesel", "TEXT_FIELD"),
            ("kraj", "SELECT"),
            ("wojewodztwo", "SELECT"),
            ("miejscowosc", "SELECT"),
            ("kod_pocztowy", "TEXT_FIELD"),
            ("powod_zlozenia_wniosku", "RADIO"),
            ("fotografia_wnioskodawcy", "FILE_UPLOADER"),
            ("dodatkowe_uwagi", "TEXTAREA"),
            ("numer_telefonu", "TEXT_FIELD"),
            ("adres_email", "TEXT_FIELD"),
            ("zgoda_1", "CONSENT_SECTION"),
        },
    },
    {
        "case_id": "ceidg_wpis",
        "expected_doc_type": "wniosek_ceidg",
        "document": {
            "title": "Wniosek o wpis do CEIDG (CEIDG-1)",
            "elements": [
                {"element_id": "c1", "page": 1, "text": "Formularz CEIDG-1"},
                {"element_id": "c2", "page": 1, "text": "Działalność gospodarcza"},
                {
                    "element_id": "c3",
                    "page": 1,
                    "label": "Nazwa firmy",
                    "text": "Nazwa firmy przedsiębiorcy",
                    "required": True,
                },
                {
                    "element_id": "c4",
                    "page": 1,
                    "label": "Data rozpoczęcia działalności",
                    "text": "Data rozpoczęcia działalności gospodarczej",
                    "required": True,
                },
                {
                    "element_id": "c5",
                    "page": 2,
                    "label": "Forma opodatkowania",
                    "text": "Forma opodatkowania: Skala / Liniowy / Ryczałt",
                    "options": ["Skala podatkowa", "Podatek liniowy", "Ryczałt"],
                    "required": True,
                },
                {
                    "element_id": "c6",
                    "page": 2,
                    "label": "Opis planowanej działalności",
                    "text": "Opis planowanej działalności (opcjonalnie)",
                    "required": False,
                },
                {
                    "element_id": "c7",
                    "page": 2,
                    "text": "Wyrażam zgodę na przetwarzanie danych osobowych (RODO).",
                },
            ],
        },
        "expected_fields": {
            ("nazwa_urzedu", "TEXT_FIELD"),
            ("w_czyim_imieniu", "RADIO"),
            ("imie", "TEXT_FIELD"),
            ("nazwisko", "TEXT_FIELD"),
            ("pesel", "TEXT_FIELD"),
            ("nazwa_podmiotu", "TEXT_FIELD"),
            ("nip_podmiotu", "TEXT_FIELD"),
            ("regon_podmiotu", "TEXT_FIELD"),
            ("plik_pelnomocnictwa", "FILE_UPLOADER"),
            ("nazwa_firmy", "TEXT_FIELD"),
            ("data_rozpoczecia_dzialalnosci", "DATEPICKER"),
            ("forma_opodatkowania", "RADIO"),
            ("opis_planowanej_dzialalnosci", "TEXTAREA"),
            ("zgoda_1", "CONSENT_SECTION"),
        },
    },
    {
        "case_id": "usuniecie_drzewa",
        "expected_doc_type": "wniosek_usuniecie_drzewa",
        "document": {
            "title": "Wniosek o zezwolenie na usunięcie drzewa lub krzewu (DRZ-01)",
            "elements": [
                {"element_id": "d1", "page": 1, "text": "Kod: DRZ-01, Do Wójta"},
                {
                    "element_id": "d2",
                    "page": 1,
                    "label": "Gatunek drzewa",
                    "text": "Gatunek drzewa lub krzewu",
                    "required": True,
                },
                {
                    "element_id": "d3",
                    "page": 1,
                    "label": "Obwód pnia",
                    "text": "Obwód pnia w cm na wysokości 130 cm",
                    "required": True,
                },
                {
                    "element_id": "d4",
                    "page": 2,
                    "label": "Przyczyna usunięcia",
                    "text": "Przyczyna usunięcia drzewa lub krzewu",
                    "required": True,
                },
                {
                    "element_id": "d5",
                    "page": 2,
                    "label": "Załącznik mapy działki",
                    "text": "Załącznik mapy działki z lokalizacją drzew",
                    "required": True,
                },
                {
                    "element_id": "d6",
                    "page": 2,
                    "text": "Oświadczam o posiadanym tytule prawnym do nieruchomości.",
                },
            ],
        },
        "expected_fields": {
            ("nazwa_organu", "TEXT_FIELD"),
            ("w_czyim_imieniu", "RADIO"),
            ("imie", "TEXT_FIELD"),
            ("nazwisko", "TEXT_FIELD"),
            ("pesel", "TEXT_FIELD"),
            ("gatunek_drzewa", "TEXT_FIELD"),
            ("obwod_pnia", "NUMBER"),
            ("przyczyna_usuniecia", "TEXTAREA"),
            ("zalacznik_mapy_dzialki", "FILE_UPLOADER"),
            ("zgoda_1", "CONSENT_SECTION"),
        },
    },
]


@dataclass
class ModelSummary:
    """Aggregated evaluation metrics for a single Eden AI LLM model."""

    model: str
    cases: int
    precision: float
    recall: float
    f1: float
    schema_compliance: float
    doc_type_top1_acc: float
    source_metadata_rate: float


def _collect_form_fields(form: dict[str, Any]) -> list[dict[str, Any]]:
    collected: list[dict[str, Any]] = []
    for tab in form.get("tabs", []):
        if tab.get("type") != "MAIN":
            continue
        for q in tab.get("questions", []):
            for child in q.get("field", {}).get("repetingSectionGrid", []):
                collected.append(child["field"])
    return collected


def evaluate_model(
    model_name: str,
    *,
    offline: bool = True,
    api_key: str = "",
    base_url: str = DEFAULT_EDENAI_BASE_URL,
) -> ModelSummary:
    """Run steps 05-08 across benchmark forms and compute quality metrics."""
    config = LLMConfig(
        model=model_name,
        api_key=api_key,
        base_url=base_url,
        offline=offline,
    )
    client = LLMClient(config)

    precisions: list[float] = []
    recalls: list[float] = []
    f1s: list[float] = []
    schema_ok = 0
    doc_type_ok = 0
    total_fields = 0
    fields_with_meta = 0

    for case in BENCHMARK_FORMS:
        doc = case["document"]
        step05 = run_step05(doc)
        if step05["doc_type"] == case["expected_doc_type"]:
            doc_type_ok += 1

        template = step05["selected_template"]
        step06 = run_step06(doc, template=template, llm_client=client)
        step07 = run_step07(doc, step06, template=template, llm_client=client)
        step08 = run_step08(step07, llm_client=client)

        if not validate_form_definition(step08):
            schema_ok += 1

        extracted = _collect_form_fields(step08)
        total_fields += len(extracted)
        fields_with_meta += sum(
            1
            for f in extracted
            if f.get("source_text") and isinstance(f.get("page"), int) and "element_id" in f
        )

        expected_pairs: set[tuple[str, str]] = case["expected_fields"]
        expected_techs = {tech for tech, _ in expected_pairs}
        pred_pairs = {
            (str(f["techName"]), str(f["type"]))
            for f in extracted
            if str(f["techName"]) in expected_techs
        }

        tp = len(pred_pairs & expected_pairs)
        fp = len(pred_pairs - expected_pairs)
        fn = len(expected_pairs - pred_pairs)

        prec = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        rec = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        denom = prec + rec
        f1 = (2 * prec * rec / denom) if denom > 0 else 0.0

        precisions.append(prec)
        recalls.append(rec)
        f1s.append(f1)

    n = len(BENCHMARK_FORMS)
    return ModelSummary(
        model=config.model,
        cases=n,
        precision=round(sum(precisions) / n, 4),
        recall=round(sum(recalls) / n, 4),
        f1=round(sum(f1s) / n, 4),
        schema_compliance=round(schema_ok / n, 4),
        doc_type_top1_acc=round(doc_type_ok / n, 4),
        source_metadata_rate=(round(fields_with_meta / total_fields, 4) if total_fields else 0.0),
    )


def compare_models(
    models: tuple[str, ...] = DEFAULT_MODELS,
    *,
    offline: bool = True,
    api_key: str = "",
    base_url: str = DEFAULT_EDENAI_BASE_URL,
) -> list[dict[str, Any]]:
    """Evaluate Eden AI models with identical metrics."""
    results = [
        evaluate_model(name, offline=offline, api_key=api_key, base_url=base_url) for name in models
    ]
    return [asdict(r) for r in results]


def format_comparison_table(results: list[dict[str, Any]]) -> str:
    """Format model metrics as a readable CLI table."""
    model_col = max(32, max((len(str(row["model"])) for row in results), default=0) + 2)
    width = model_col + 50
    header = (
        f"{'Model':<{model_col}}"
        f"{'Prec':>8}{'Rec':>8}{'F1':>8}{'Schema':>9}{'DocTop1':>9}{'Meta':>8}"
    )
    lines = [
        "=== Eden AI LLM Comparison (Steps 05-08, COI Standard) ===",
        "",
        header,
        "-" * width,
    ]
    for row in results:
        lines.append(
            f"{row['model']:<{model_col}}"
            f"{row['precision']:>8.4f}{row['recall']:>8.4f}{row['f1']:>8.4f}"
            f"{row['schema_compliance']:>9.4f}{row['doc_type_top1_acc']:>9.4f}"
            f"{row['source_metadata_rate']:>8.4f}"
        )
    lines.append("-" * width)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compare Eden AI LLM models on COI form extraction."
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Call live Eden AI API instead of offline mock mode (requires EDENAI_API_KEY).",
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("EDENAI_BASE_URL", DEFAULT_EDENAI_BASE_URL),
        help="Eden AI endpoint base URL (used with --live).",
    )
    parser.add_argument(
        "--models",
        nargs="+",
        default=list(DEFAULT_MODELS),
        help="Eden AI model identifiers to compare (provider/model).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output comparison results as JSON.",
    )
    args = parser.parse_args(argv)

    results = compare_models(
        tuple(args.models),
        offline=not args.live,
        api_key=os.getenv("EDENAI_API_KEY", ""),
        base_url=args.base_url,
    )
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print(format_comparison_table(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
