"""Wspólne typy i pomocnicze narzędzia robocze dla ekstraktorów dokumentów."""

import os
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from backend.shared.config import Settings
from backend.shared.schemas.pipeline import DocumentFormat, ExtractedDocument


@dataclass(frozen=True, slots=True)
class ExtractionContext:
    """Niemutowalny kontekst przekazywany do konkretnego handlera formatu."""

    content: bytes
    filename: str
    extension: str
    mime_type: str
    document_format: DocumentFormat
    sha256: str
    settings: Settings


ExtractorHandler = Callable[[ExtractionContext], ExtractedDocument]


@contextmanager
def temporary_document_file(
    content: bytes,
    *,
    extension: str,
    temp_dir: Path | None = None,
) -> Iterator[Path]:
    """Zapisuje dokument w izolowanym katalogu tymczasowym i gwarantuje jego usunięcie.

    Nazwa pliku na dysku jest zawsze generowana lokalnie (``document.<ext>``) — nigdy nie
    pochodzi od nazwy przesłanej przez użytkownika. Katalog wraz z zawartością jest usuwany
    automatycznie przy wyjściu z kontekstu, także w przypadku wystąpienia wyjątku.
    """
    safe_ext = "".join(ch for ch in extension.lower() if ch.isalnum()) or "bin"
    with tempfile.TemporaryDirectory(prefix="coi-extract-", dir=temp_dir) as workdir:
        if os.name != "nt":
            os.chmod(workdir, 0o700)
        file_path = Path(workdir) / f"document.{safe_ext}"
        file_path.write_bytes(content)
        yield file_path


def build_skeleton_document(
    ctx: ExtractionContext,
    *,
    warnings: list[str] | None = None,
    metadata: dict[str, str] | None = None,
) -> ExtractedDocument:
    """Buduje poprawny obiekt ``ExtractedDocument`` z metadanymi wejściowymi."""
    return ExtractedDocument(
        document_sha256=ctx.sha256,
        filename=ctx.filename,
        extension=ctx.extension,
        mime_type=ctx.mime_type,
        document_format=ctx.document_format,
        page_count=0,
        warnings=warnings or [],
        metadata=metadata or {},
    )
