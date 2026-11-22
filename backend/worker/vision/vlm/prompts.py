"""Prompty VLM jako pliki w repozytorium (``backend/worker/vision/prompts/``) i ich wypełnianie.

- Wersja promptu = nazwa pliku i skrót SHA-256 treści (wchodzi do klucza cache), więc zmiana promptu
  unieważnia cache i uruchamia regresję.
- **Prompt injection:** tekst dokumentu jest wstawiany wyłącznie do bloku danych między znacznikami
  ``<<<DATA_BEGIN>>>`` i ``<<<DATA_END>>>``, z instrukcją ignorowania poleceń w nim zawartych. Każda
  wartość jest kodowana jako JSON (cudzysłowy i nowe linie są escapowane), a sekwencje ``<<<``
  i ``>>>`` w danych są zamieniane, żeby dane nie mogły zamknąć bloku. Podstawienie jest
  jednoprzebiegowe: wstawione dane nie są ponownie interpretowane jako znaczniki szablonu.
"""

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts"
PLACEHOLDER = re.compile(r"\{\{([A-Z_]+)\}\}")
DATA_MARKERS = ("<<<", ">>>")


@dataclass(frozen=True)
class Prompt:
    name: str
    template: str

    @property
    def version(self) -> str:
        digest = hashlib.sha256(self.template.encode("utf-8")).hexdigest()[:12]
        return f"{self.name}:{digest}"

    def render(self, values: Mapping[str, str]) -> str:
        missing = set(PLACEHOLDER.findall(self.template)) - set(values)
        if missing:
            raise KeyError(f"brak wartości dla {sorted(missing)} w prompcie {self.name}")
        return PLACEHOLDER.sub(lambda match: values[match.group(1)], self.template)


@cache
def load_prompt(name: str) -> Prompt:
    path = PROMPTS_DIR / f"{name}.txt"
    return Prompt(name=name, template=path.read_text(encoding="utf-8"))


def neutralize(text: str) -> str:
    """Usuwa z danych sekwencje znaczników bloku danych."""
    return text.replace("<<<", "‹‹‹").replace(">>>", "›››")


def data_lines(rows: list[dict[str, object]]) -> str:
    """Wiersze bloku danych: jeden obiekt JSON na wiersz, bez znaczników bloku."""
    if not rows:
        return "(none)"
    return "\n".join(neutralize(json.dumps(row, ensure_ascii=False)) for row in rows)
