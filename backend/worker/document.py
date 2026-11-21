"""Dokument w trakcie analizy — treść w pamięci i kopia w katalogu tymczasowym (tmpfs).

Część bibliotek (Docling, LibreOffice, pypdfium2) wymaga ścieżki do pliku, dlatego worker
zapisuje plik w prywatnym katalogu tymczasowym, który jest usuwany po analizie — także
po błędzie. Nazwa pliku na dysku nie pochodzi od użytkownika.
"""

import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from backend.shared.jobs import DocumentRef


@dataclass(frozen=True, slots=True)
class IncomingDocument:
    ref: DocumentRef
    content: bytes
    path: Path


@contextmanager
def document_workspace(
    ref: DocumentRef, content: bytes, temp_dir: Path | None = None
) -> Iterator[IncomingDocument]:
    with tempfile.TemporaryDirectory(prefix="coi-", dir=temp_dir) as workdir:
        os.chmod(workdir, 0o700)
        path = Path(workdir) / f"document.{ref.extension}"
        path.write_bytes(content)
        yield IncomingDocument(ref=ref, content=content, path=path)
