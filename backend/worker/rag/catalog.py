"""Katalog typów dokumentów — nasze formularze od organizatora (``catalog/types.json``).

Każdy typ ma tytuł, słowa kluczowe (dopasowanie leksykalne), sygnaturę dokumentu (wektor
w bazie wiedzy) i — jeśli istnieje — konfigurację COI (wzorzec dla modelu). Katalog trafia do
bazy przy starcie workera; formularze zatwierdzane przez urzędników dochodzą do niej w pętli
zwrotnej.
"""

import hashlib
import json
from functools import cache
from pathlib import Path
from typing import Any

CATALOG_DIR = Path(__file__).with_name("catalog")
TYPES_PATH = CATALOG_DIR / "types.json"


@cache
def _load() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(TYPES_PATH.read_text(encoding="utf-8"))
    return data


def catalog_types() -> list[dict[str, Any]]:
    """Typy z katalogu w formacie wzorca etapu 05 (``template_id``, ``doc_type``, ``title`` …)."""
    types: list[dict[str, Any]] = []
    for entry in _load()["types"]:
        types.append(
            {
                "template_id": f"tpl_{entry['doc_type']}",
                "code": entry.get("code") or "",
                "doc_type": entry["doc_type"],
                "title": entry["title"],
                "keywords": list(entry.get("keywords") or []),
                # Standard COI: każdy wniosek można złożyć w imieniu innej osoby lub podmiotu.
                "supports_proxy": True,
                "default_authority": "Urząd właściwy",
                "subject_section_title": entry.get("subject_section_title") or "Przedmiot wniosku",
                "expected_fields": [],
                "signature": entry.get("signature") or "",
                "form_path": entry.get("form"),
            }
        )
    return types


def catalog_form(entry: dict[str, Any]) -> dict[str, Any] | None:
    """Konfiguracja COI typu (wzorzec), jeśli katalog ją zawiera."""
    relative = entry.get("form_path")
    if not relative:
        return None
    data: dict[str, Any] = json.loads((CATALOG_DIR / str(relative)).read_text(encoding="utf-8"))
    return data


def signature_sha256(signature: str) -> str:
    return hashlib.sha256(signature.encode("utf-8")).hexdigest()
