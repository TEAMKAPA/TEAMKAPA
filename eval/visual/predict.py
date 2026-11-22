"""Predykcje detektora dla stron zbioru #12 (pliki ``PagePredictions`` dla ``eval.visual.run``).

Użycie::

    python -m eval.visual.predict --system opencv
    python -m eval.visual.run --system opencv --predictions dane/robocze/predictions/opencv-dev

Każdy plik wariantu jest renderowany z ``dane/`` (ścieżki z pełnego manifestu) tak samo jak przy
przygotowaniu zbioru, a obraz strony oznaczanej musi mieć hash z manifestu. Domyślnie powstają
predykcje oznaczonych zestawów etykiet części ``dev`` (``--all-pages``: wszystkich stron części).
Część ``holdout`` wymaga ``--split holdout --final``; uruchomienie jest dopisywane do
``holdout_runs.log``. Przy ``dev`` skrypt nie otwiera plików ani etykiet ``holdout``.
"""

import argparse
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from backend.worker.vision.config import RenderConfig
from backend.worker.vision.detect import detect_page
from backend.worker.vision.errors import RenderError
from backend.worker.vision.models import RenderedDocument
from backend.worker.vision.render import render_document
from eval.visual.geometry import image_sha256
from eval.visual.models import (
    PREDICTIONS_SCHEMA,
    Manifest,
    ManifestDocument,
    ManifestFile,
    PagePredictions,
    Split,
    System,
    dump_json,
    predictions_file_name,
    read_json,
    write_text,
)
from eval.visual.prepare import PrepareError, git_state
from eval.visual.run import DEFAULT_LABELS, DEFAULT_MANIFEST, DEFAULT_OUT, HOLDOUT_LOG, EvalError

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DANE = REPO_ROOT / "dane"
DEFAULT_PREDICTIONS = REPO_ROOT / "dane" / "robocze" / "predictions"
SUPPORTED = (System.OPENCV,)


def _render(
    document: ManifestDocument, file: ManifestFile, dane: Path, dpi: int, isolated: bool
) -> RenderedDocument:
    if file.path is None:
        raise EvalError(f"{file.file_id}: manifest bez ścieżek (użyj manifest.full.json)")
    data = (dane / file.path).read_bytes()
    return render_document(data, file.declared_kind, RenderConfig(dpi=dpi), isolated=isolated)


def predict(
    manifest: Manifest,
    split: Split,
    system: System,
    out: Path,
    *,
    dane: Path = DEFAULT_DANE,
    labels: Path = DEFAULT_LABELS,
    all_pages: bool = False,
    isolated: bool = True,
) -> list[str]:
    """Zapisuje pliki predykcji; zwraca ich nazwy (posortowane)."""
    if system not in SUPPORTED:
        raise EvalError(f"system {system.value} nie ma jeszcze implementacji")
    written: list[str] = []
    for document in manifest.documents:
        if document.split is not split:
            continue
        pages = [
            p for p in document.pages if all_pages or (labels / f"{p.label_id}.json").is_file()
        ]
        files = {f.file_id: f for f in document.files}
        needed = sorted({v.file_id for p in pages for v in p.variants if v.included})
        rendered = {
            file_id: _render(document, files[file_id], dane, manifest.renderer.dpi, isolated)
            for file_id in needed
        }
        for page in pages:
            base = rendered[page.base_file_id].pages[page.page - 1]
            if image_sha256(base.image) != page.image_sha256:
                raise EvalError(f"{page.label_id}: obraz strony różni się od manifestu")
            for variant in page.variants:
                if not variant.included:
                    continue
                rendered_page = rendered[variant.file_id].pages[variant.page - 1]
                detection = detect_page(rendered_page)
                predictions = PagePredictions(
                    schema_version=PREDICTIONS_SCHEMA,
                    system=system,
                    file_id=variant.file_id,
                    page=variant.page,
                    width_px=rendered_page.meta.width_px,
                    height_px=rendered_page.meta.height_px,
                    elements=detection.elements,
                )
                name = predictions_file_name(variant.file_id, variant.page)
                write_text(out / name, dump_json(predictions))
                written.append(name)
    return sorted(written)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Predykcje detektora dla zbioru #12")
    parser.add_argument("--system", required=True, choices=[s.value for s in SUPPORTED])
    parser.add_argument("--split", default=Split.DEV.value, choices=[s.value for s in Split])
    parser.add_argument("--final", action="store_true", help="wymagane dla holdout")
    parser.add_argument("--all-pages", action="store_true", help="także strony bez etykiet")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--labels", type=Path, default=DEFAULT_LABELS)
    parser.add_argument("--dane", type=Path, default=DEFAULT_DANE)
    parser.add_argument(
        "--out", type=Path, default=None, help="domyślnie predictions/<system>-<split>"
    )
    parser.add_argument("--log-dir", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)
    split, system = Split(args.split), System(args.system)
    out = args.out or DEFAULT_PREDICTIONS / f"{system.value}-{split.value}"
    try:
        if split is Split.HOLDOUT and not args.final:
            raise EvalError("holdout tylko z --final (uruchomienie jest odnotowywane)")
        if split is Split.DEV and args.final:
            raise EvalError("--final dotyczy wyłącznie holdout")
        manifest = read_json(args.manifest, Manifest)
        if split is Split.HOLDOUT:
            commit, _ = git_state(REPO_ROOT)
            stamp = datetime.now().isoformat(timespec="seconds")
            args.log_dir.mkdir(parents=True, exist_ok=True)
            with (args.log_dir / HOLDOUT_LOG).open("a", encoding="utf-8", newline="\n") as log:
                log.write(f"{stamp}\t{commit}\tpredict:{system.value}\t{out}\n")
        written = predict(
            manifest,
            split,
            system,
            out,
            dane=args.dane,
            labels=args.labels,
            all_pages=args.all_pages,
        )
    except (EvalError, PrepareError, RenderError, ValueError, OSError) as error:
        print(f"BŁĄD: {error}", file=sys.stderr)
        return 2
    print(f"{system.value} / {split.value}: {len(written)} plików predykcji w {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
