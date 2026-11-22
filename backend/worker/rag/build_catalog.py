"""Uzupełnia sygnatury typów w ``catalog/types.json`` z formularzy od organizatora.

    python -m backend.worker.rag.build_catalog --source "C:/.../Dane do KAPAForms"

Dla każdego typu bierze plik z folderu ``source_folder`` (pierwszeństwo: DOCX, potem DOC,
PDF, ODS), wyodrębnia treść tym samym ekstraktorem co pipeline i zapisuje sygnaturę
(tytuł + początek treści, jak przy rozpoznawaniu). Sygnatury to fragmenty publicznych
formularzy urzędowych; treści dokumentów użytkowników nie trafiają do katalogu ani do bazy.
"""

import argparse
import json
from pathlib import Path

from backend.worker.pipeline.step02_text_extract import extract_document
from backend.worker.pipeline.text_extraction import to_contract
from backend.worker.rag.catalog import TYPES_PATH
from backend.worker.rag.embedder import document_signature

_PREFERENCE = (".docx", ".doc", ".pdf", ".ods", ".xlsx")
_MIME = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".doc": "application/msword",
    ".pdf": "application/pdf",
    ".ods": "application/vnd.oasis.opendocument.spreadsheet",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


def _pick(folder: Path) -> Path:
    files = [p for p in folder.iterdir() if p.is_file() and "wizualizacja" not in p.name.lower()]
    for suffix in _PREFERENCE:
        for path in sorted(files):
            if path.suffix.lower() == suffix:
                return path
    raise FileNotFoundError(f"Brak obsługiwanego pliku w {folder}")


def signature_of(path: Path) -> str:
    content = path.read_bytes()
    suffix = path.suffix.lower()
    if suffix == ".doc" and content.startswith(b"PK"):
        suffix = ".docx"  # .DOC z treścią DOCX (spotykane w materiałach organizatora)
    result = extract_document(content, filename=f"document{suffix}", mime_type=_MIME[suffix])
    return document_signature(to_contract(result).as_step_payload())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", type=Path, required=True)
    args = parser.parse_args()

    data = json.loads(TYPES_PATH.read_text(encoding="utf-8"))
    folders = [p for p in args.source.iterdir() if p.is_dir()]
    for entry in data["types"]:
        (folder,) = [f for f in folders if f.name.startswith(entry["source_folder"])]
        path = _pick(folder)
        entry["signature"] = signature_of(path)
        print(f"{entry['doc_type']:34s} ← {path.name} ({len(entry['signature'])} znaków)")
    TYPES_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )


if __name__ == "__main__":
    main()
