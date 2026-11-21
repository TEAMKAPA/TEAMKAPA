"""Label Studio: przypięta wersja, nazwy kontrolek konfiguracji i format zadań (#12).

Label Studio jest narzędziem człowieka do oznaczania, a nie zależnością rozwiązania: działa
w osobnym kontenerze Dockera (instrukcja w ``eval/visual/README.md``) i nie występuje
w ``requirements*.txt``. Konfiguracja oznaczania: ``eval/visual/labelstudio/config.xml``,
ustawienia prywatności serwera: ``eval/visual/labelstudio/ls.env``.
"""

import hashlib
from pathlib import Path
from typing import Final

LS_NAME: Final = "label-studio"
LS_VERSION: Final = "1.23.2"
LS_IMAGE: Final = (
    "heartexlabs/label-studio:1.23.2"
    "@sha256:afcc516a22775a39d0d66f4c2ddc95d01be3e3d3862b21fcf4f9de34b4ad4e12"
)
"""Obraz Community (Apache-2.0), wydanie 1.23.2 z 2026-09-29; digest listy manifestów."""

LABELSTUDIO_DIR: Final = Path(__file__).resolve().parent / "labelstudio"
CONFIG_PATH: Final = LABELSTUDIO_DIR / "config.xml"
ENV_PATH: Final = LABELSTUDIO_DIR / "ls.env"

# Nazwy kontrolek w config.xml (konwerter eksportu korzysta z tych samych stałych).
IMAGE_TAG: Final = "image"
KIND_CONTROL: Final = "kind"
CELLS_CONTROL: Final = "cells"
ROLE_CONTROL: Final = "role"
MULTI_CONTROL: Final = "multi"
FLAGS_CONTROL: Final = "flags"
PAGE_STATUS_CONTROL: Final = "page_status"
UNCERTAIN_FLAG: Final = "uncertain"
MULTI_VALUES: Final = ("single", "multi", "unknown")

PAGES_DIR_IN_CONTAINER: Final = "pages"
"""Podkatalog ``LOCAL_FILES_DOCUMENT_ROOT`` z obrazami stron (``/label-studio/files/pages``)."""


def config_sha256(path: Path = CONFIG_PATH) -> str:
    """Hash konfiguracji oznaczania (zapisywany w etykietach i manifeście)."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def image_url(label_id: str) -> str:
    """Adres obrazu strony w zadaniu Label Studio (serwowanie plików lokalnych)."""
    return f"/data/local-files/?d={PAGES_DIR_IN_CONTAINER}/{label_id}.png"


def page_file_name(label_id: str) -> str:
    return f"{label_id}.png"
