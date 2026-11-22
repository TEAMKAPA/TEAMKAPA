"""Ewaluacja etapu VLM (#11) na oznaczonych stronach ``dev`` (wariant bazowy, PDF).

Użycie::

    python -m eval.visual.vlm_eval --dry-run            # bez wywołań: żądania i szacunek tokenów
    python -m eval.visual.vlm_eval --mode max           # prawdziwe wywołania (cache w dane/robocze)
    python -m eval.visual.vlm_eval --mode balanced

Strony: oznaczone zestawy etykiet części ``dev``, tylko strona bazowa (render PDF). Bloki tekstu
z warstwy tekstowej PDF (``text_blocks.py``, tymczasowo do czasu #7), pola z tekstu z oczekiwanego
JSON-a dokumentu, gdy istnieje (do czasu #8). Narzędzie nie ma opcji ``holdout``.

Wynik w ``dane/robocze/``: predykcje ``fusion`` (elementy po trybie 1) w
``predictions/fusion-dev-pdf/``, raport metryk OpenCV i fuzji na tych samych stronach oraz raport
przeglądu i liczniki wywołań w ``eval/vlm-dev-<tryb>.json`` i ``.md``.
"""

import argparse
import json
import logging
import math
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image as PILImage

from backend.worker.vision.config import RenderConfig
from backend.worker.vision.detect import detect_page
from backend.worker.vision.models import RenderedPage
from backend.worker.vision.render import render_document
from backend.worker.vision.vlm.cache import CachedVlmClient, VlmStats
from backend.worker.vision.vlm.client import (
    VlmClient,
    VlmError,
    VlmRequest,
    VlmResponse,
    VlmUnavailable,
    VlmUsage,
    build_client,
    generation_params,
)
from backend.worker.vision.vlm.config import AccuracyMode, load_vlm_settings
from backend.worker.vision.vlm.double_read import FormField, ReferenceJsonFieldSource
from backend.worker.vision.vlm.stage import PageInput, run_vlm_stage
from eval.visual.geometry import image_sha256
from eval.visual.match import MatchParams, from_label, from_prediction
from eval.visual.metrics import PageCase
from eval.visual.models import (
    PREDICTIONS_SCHEMA,
    FileStatus,
    Manifest,
    ManifestDocument,
    ManifestPage,
    PagePredictions,
    Split,
    System,
    dump_json,
    predictions_file_name,
    read_json,
    write_text,
)
from eval.visual.prepare import git_state
from eval.visual.run import (
    DEFAULT_LABELS,
    DEFAULT_MANIFEST,
    DEFAULT_OUT,
    build_report,
    load_dataset,
)
from eval.visual.text_blocks import pdf_text_blocks

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DANE = REPO_ROOT / "dane"
PREDICTIONS = REPO_ROOT / "dane" / "robocze" / "predictions" / "fusion-dev-pdf"
PRICE_INPUT = 0.15 / 1_000_000  # USD za token (katalog Eden AI, 2026-11-22)
PRICE_OUTPUT = 0.60 / 1_000_000
TOKENS_PER_ELEMENT = 30  # szacunek wyjścia trybu 1
TOKENS_PER_FIELD = 45  # szacunek wyjścia trybu 2


@dataclass(frozen=True)
class Document:
    manifest: ManifestDocument
    pages: list[tuple[str, RenderedPage]]  # (label_id, strona bazowa)
    blocks: list[Any]
    text_fields: list[FormField] | None
    labeled: set[str]  # label_id stron z etykietami (metryki)


def _reference_fields(document: ManifestDocument, dane: Path) -> list[FormField] | None:
    for file in document.files:
        if file.status is FileStatus.REFERENCE and file.format == "json" and file.path:
            data = json.loads((dane / file.path).read_text(encoding="utf-8"))
            return ReferenceJsonFieldSource(data).fields()
    return None


def load_documents(
    manifest: Manifest, labels: Path, dane: Path, extra: Sequence[str] = ()
) -> list[Document]:
    """Oznaczone strony dev oraz ``extra`` (nieoznaczone strony dev: etap VLM bez metryk)."""
    dataset = load_dataset(manifest, labels, Split.DEV)
    wanted: dict[str, list[tuple[ManifestDocument, ManifestPage, bool]]] = {}
    for item in dataset.labeled:
        wanted.setdefault(item.document.doc_id, []).append((item.document, item.page, True))
    for label_id in extra:
        document, page = manifest.find_page(label_id)
        if document.split is not Split.DEV:
            raise ValueError(f"{label_id}: tylko strony części dev")
        if label_id not in {p.label_id for _, p, _ in wanted.get(document.doc_id, [])}:
            wanted.setdefault(document.doc_id, []).append((document, page, False))
    documents = []
    for doc_id in sorted(wanted):
        items = sorted(wanted[doc_id], key=lambda item: item[1].page)
        manifest_doc = items[0][0]
        files = {f.file_id: f for f in manifest_doc.files}
        pages: list[tuple[str, RenderedPage]] = []
        labeled: set[str] = set()
        blocks: list[Any] = []
        rendered_cache: dict[str, Any] = {}
        for _, manifest_page, is_labeled in items:
            file = files[manifest_page.base_file_id]
            if file.path is None:
                raise ValueError("manifest bez ścieżek: użyj manifest.full.json")
            data = (dane / file.path).read_bytes()
            if file.file_id not in rendered_cache:
                rendered_cache[file.file_id] = render_document(
                    data, file.declared_kind, RenderConfig(dpi=manifest.renderer.dpi)
                )
            page = rendered_cache[file.file_id].pages[manifest_page.page - 1]
            if image_sha256(page.image) != manifest_page.image_sha256:
                raise ValueError(f"{manifest_page.label_id}: obraz różni się od manifestu")
            pages.append((manifest_page.label_id, page))
            if is_labeled:
                labeled.add(manifest_page.label_id)
            if file.format == "pdf":
                blocks.extend(pdf_text_blocks(data, manifest_page.page))
        documents.append(
            Document(manifest_doc, pages, blocks, _reference_fields(manifest_doc, dane), labeled)
        )
    return documents


class StopAfterError:
    """Bezpiecznik: po pierwszym błędzie VLM kolejne wywołania nie idą do sieci."""

    def __init__(self, inner: VlmClient) -> None:
        self._inner = inner
        self.name = inner.name
        self.model = inner.model
        self.error: VlmError | None = None

    def complete(self, request: VlmRequest) -> VlmResponse:
        if self.error is not None:
            raise VlmUnavailable(f"przerwane po wcześniejszym błędzie ({self.error.detail})")
        try:
            return self._inner.complete(request)
        except VlmError as error:
            self.error = error
            raise


class RecordingClient:
    """Dry-run: zbiera żądania i zwraca minimalną poprawną odpowiedź (bez sieci)."""

    name = "dry-run"

    def __init__(self, model: str) -> None:
        self.model = model
        self.requests: list[VlmRequest] = []

    def complete(self, request: VlmRequest) -> VlmResponse:
        self.requests.append(request)
        content = '{"fields": []}' if request.task == "fields" else '{"elements": [], "added": []}'
        return VlmResponse(content=content, model=self.model, usage=VlmUsage())


def image_tokens(request: VlmRequest) -> int:
    """Szacunek tokenów obrazu Qwen3-VL: jeden token na 32 × 32 px (łata 16 px, scalanie 2 × 2)."""
    from io import BytesIO

    with PILImage.open(BytesIO(request.image.data)) as image:
        width, height = image.size
    return math.ceil(width / 32) * math.ceil(height / 32) + 2


def estimate(requests: Sequence[VlmRequest]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for request in requests:
        elements = request.prompt.count('{"id": "cv-p')  # elementy w bloku danych trybu 1
        text = math.ceil(len(request.prompt) / 3.2)  # tekst i JSON w promptach: ok. 3,2 znaku/token
        img = image_tokens(request)
        out = (
            TOKENS_PER_ELEMENT * elements + 50 if request.task == "roles" else TOKENS_PER_FIELD * 40
        )
        rows.append(
            {
                "task": request.task,
                "prompt_tokens": text + img,
                "image_tokens": img,
                "output_tokens": out,
            }
        )
    prompt = sum(int(r["prompt_tokens"]) for r in rows)
    output = sum(int(r["output_tokens"]) for r in rows)
    return {
        "calls": len(rows),
        # Każda odpowiedź niezgodna ze schematem: jedna ponowna próba.
        "calls_worst_case": 2 * len(rows),
        "prompt_tokens": prompt,
        "output_tokens": output,
        "cost_usd": round(prompt * PRICE_INPUT + output * PRICE_OUTPUT, 4),
        "cost_usd_worst_case": round(2 * (prompt * PRICE_INPUT + output * PRICE_OUTPUT), 4),
        "requests": rows,
    }


def _cases(
    document: Document, elements_by_page: dict[int, tuple[Any, ...]], labels: Path
) -> list[PageCase]:
    from eval.visual.models import PageLabels

    cases = []
    for label_id, page in document.pages:
        if label_id not in document.labeled:
            continue
        gt = read_json(labels / f"{label_id}.json", PageLabels)
        cases.append(
            PageCase(
                label_id=label_id,
                doc_id=document.manifest.doc_id,
                file_id=gt.source.file_id,
                page=page.meta.page,
                format_group="pdf",
                width_px=page.meta.width_px,
                height_px=page.meta.height_px,
                gt=tuple(from_label(e) for e in gt.elements),
                pred=tuple(from_prediction(e) for e in elements_by_page[page.meta.page]),
            )
        )
    return cases


def run(args: argparse.Namespace) -> int:
    mode = AccuracyMode(args.mode) if args.mode else None
    settings = load_vlm_settings(**({"accuracy_mode": mode} if mode else {}))
    manifest = read_json(args.manifest, Manifest)
    documents = load_documents(manifest, args.labels, args.dane, args.extra)
    dataset = load_dataset(manifest, args.labels, Split.DEV)

    if args.dry_run:
        recorder = RecordingClient(settings.model)
        for document in documents:
            pages = [
                PageInput(p.meta.page, p.image, detect_page(p).elements) for _, p in document.pages
            ]
            run_vlm_stage(pages, document.blocks, document.text_fields, recorder, settings)
        result = estimate(recorder.requests)
        result["mode"] = settings.accuracy_mode.value
        result["model"] = settings.model
        result["pages"] = sum(len(d.pages) for d in documents)
        result["text_blocks"] = sum(len(d.blocks) for d in documents)
        result["reference_fields"] = {
            d.manifest.doc_id: len(d.text_fields or []) for d in documents
        }
        write_text(
            args.out / f"vlm-dev-{settings.accuracy_mode.value}-dry-run.json",
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        )
        print(json.dumps({k: v for k, v in result.items() if k != "requests"}, ensure_ascii=False))
        return 0

    stats = VlmStats()
    guard = StopAfterError(build_client(settings))
    client: VlmClient = CachedVlmClient(
        guard,
        generation_params(settings),
        settings.cache_dir if settings.cache_enabled else None,
        stats=stats,
    )
    started = time.monotonic()
    opencv_cases: list[PageCase] = []
    fusion_cases: list[PageCase] = []
    reviews: list[dict[str, Any]] = []
    for document in documents:
        detections = {p.meta.page: detect_page(p) for _, p in document.pages}
        pages = [
            PageInput(
                p.meta.page,
                p.image,
                detections[p.meta.page].elements,
                detections[p.meta.page].reasons,
            )
            for _, p in document.pages
        ]
        stage = run_vlm_stage(pages, document.blocks, document.text_fields, client, settings)
        by_page: dict[int, tuple[Any, ...]] = {}
        for element in stage.elements:
            by_page.setdefault(element.page, ())
            by_page[element.page] = (*by_page[element.page], element)
        opencv_cases += _cases(
            document, {n: d.elements for n, d in detections.items()}, args.labels
        )
        fusion_cases += _cases(document, by_page, args.labels)
        for _, page in document.pages:
            predictions = PagePredictions(
                schema_version=PREDICTIONS_SCHEMA,
                system=System.FUSION,
                file_id=f"{document.manifest.doc_id}-pdf",
                page=page.meta.page,
                width_px=page.meta.width_px,
                height_px=page.meta.height_px,
                elements=by_page.get(page.meta.page, ()),
            )
            write_text(
                PREDICTIONS / predictions_file_name(predictions.file_id, page.meta.page),
                dump_json(predictions),
            )
        reviews.append({"doc_id": document.manifest.doc_id, **stage.report()})
    elapsed = time.monotonic() - started
    commit = git_state(REPO_ROOT)
    params = MatchParams()
    reports = {
        system.value: build_report(cases, dataset, system, params, commit)
        for system, cases in ((System.OPENCV, opencv_cases), (System.FUSION, fusion_cases))
    }
    summary = {
        "mode": settings.accuracy_mode.value,
        "model": settings.model,
        "pages": len(fusion_cases),
        "duration_s": round(elapsed, 2),
        "vlm_stats": stats.as_dict(),
        "metrics": {
            name: {
                "micro": report["overall"]["micro"],
                "roles": {k: v for k, v in report["overall"]["roles"].items() if k != "confusion"},
                "role_confusion": report["overall"]["roles"]["confusion"],
            }
            for name, report in reports.items()
        },
        "review": reviews,
    }
    stem = f"vlm-dev-{settings.accuracy_mode.value}"
    write_text(args.out / f"{stem}.json", json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {
                "mode": summary["mode"],
                "pages": summary["pages"],
                "duration_s": summary["duration_s"],
                "vlm_stats": summary["vlm_stats"],
                "fusion_micro_f1": reports["fusion"]["overall"]["micro"]["f1"],
                "fusion_role_accuracy": reports["fusion"]["overall"]["roles"]["accuracy"],
                "review_codes": [r["codes"] for r in reviews],
            },
            ensure_ascii=False,
        )
    )
    print(f"Raport: {args.out / (stem + '.json')}")
    if guard.error is not None:
        print(f"BŁĄD VLM: {guard.error.code}: {guard.error.detail} (dalsze wywołania wstrzymane)")
        return 1
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ewaluacja etapu VLM na stronach dev")
    parser.add_argument("--mode", choices=[m.value for m in AccuracyMode], default=None)
    parser.add_argument("--dry-run", action="store_true", help="bez wywołań: szacunek tokenów")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--labels", type=Path, default=DEFAULT_LABELS)
    parser.add_argument("--dane", type=Path, default=DEFAULT_DANE)
    parser.add_argument(
        "--extra", nargs="*", default=[], help="nieoznaczone strony dev (etap VLM bez metryk)"
    )
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # adresy żądań bez znaczenia w logu
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
