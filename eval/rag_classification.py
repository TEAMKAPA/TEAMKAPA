"""Ewaluacja rozpoznawania typu dokumentu przez RAG (kNN na wektorach e5).

Dla każdego pliku z folderów organizatora (wszystkie formaty: PDF, DOC, DOCX, ODS, XLSX;
obrazy wymagają OCR — Tesseract) liczy sygnaturę i podobieństwo do typów z katalogu:

- top-1: czy najbliższy typ to właściwy typ (katalog zawiera ten typ),
- leave-one-type-out: typ dokumentu usunięty z katalogu — najwyższe podobieństwo do
  innego typu pokazuje, gdzie postawić próg „nowy typ” (``RAG_REJECT_SIMILARITY``).

    python eval/rag_classification.py --source "C:/.../Dane do KAPAForms"
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.shared.config import Settings  # noqa: E402
from backend.worker.rag.build_catalog import signature_of  # noqa: E402
from backend.worker.rag.catalog import catalog_types  # noqa: E402
from backend.worker.rag.embedder import E5Embedder  # noqa: E402

_FORMATS = (".pdf", ".doc", ".docx", ".ods", ".xlsx")


def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", type=Path, required=True)
    args = parser.parse_args()

    settings = Settings()
    embedder = E5Embedder(settings.embedding_model, settings.embedding_model_revision)
    types = catalog_types()
    catalog = dict(
        zip(
            [t["doc_type"] for t in types],
            embedder.embed([t["signature"] for t in types]),
            strict=True,
        )
    )
    folder_type = {
        t["doc_type"]: next(f for f in args.source.iterdir() if f.name.startswith(prefix))
        for t, prefix in ((t, _prefix(t)) for t in types)
    }

    correct = total = 0
    own: list[float] = []
    margins: list[float] = []
    foreign: list[float] = []
    for doc_type, folder in folder_type.items():
        for path in sorted(folder.iterdir()):
            if path.suffix.lower() not in _FORMATS or "wizualizacja" in path.name.lower():
                continue
            try:
                vector = embedder.embed([signature_of(path)])[0]
            except Exception as exc:
                print(f"  {path.name[:60]:60s} BŁĄD {type(exc).__name__}")
                continue
            ranked = sorted(((_dot(vector, v), t) for t, v in catalog.items()), reverse=True)
            (best_sim, best), (second_sim, _) = ranked[0], ranked[1]
            others = max(s for s, t in ranked if t != doc_type)
            total += 1
            correct += best == doc_type
            own.append(next(s for s, t in ranked if t == doc_type))
            margins.append(best_sim - second_sim)
            foreign.append(others)
            mark = "OK " if best == doc_type else "ŹLE"
            print(
                f"{mark} {path.name[:58]:58s} → {best:32s} {best_sim:.3f} "
                f"(2.: {second_sim:.3f}, najlepszy obcy: {others:.3f})"
            )

    print(f"\ntop-1: {correct}/{total} = {correct / total:.3f}")
    print(f"podobieństwo do własnego typu: min {min(own):.3f}, mediana {_median(own):.3f}")
    print(f"najwyższe podobieństwo do obcego typu (leave-one-type-out): max {max(foreign):.3f}")
    print(f"margines top-1 − top-2: min {min(margins):.3f}")


def _prefix(entry: dict[str, object]) -> str:
    from backend.worker.rag.catalog import _load

    return str(
        next(t for t in _load()["types"] if t["doc_type"] == entry["doc_type"])["source_folder"]
    )


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[len(ordered) // 2]


if __name__ == "__main__":
    main()
