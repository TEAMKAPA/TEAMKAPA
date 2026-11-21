"""Handler ekstrakcji arkuszy OpenDocument (.ods) — przetwarzanie w pamięci (odfdo)."""

import io
import zipfile

from backend.shared.schemas.pipeline import ExtractedDocument
from backend.worker.pipeline.extractors.base import ExtractionContext, build_skeleton_document
from backend.worker.pipeline.extractors.exceptions import CorruptedFileError, EncryptedFileError

_ODS_MIMETYPE = b"application/vnd.oasis.opendocument.spreadsheet"
_MAX_MANIFEST_BYTES = 2 * 1024 * 1024


def _validate_ods_bytes(content: bytes) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            names = set(archive.namelist())
            if "META-INF/manifest.xml" in names:
                manifest_info = archive.getinfo("META-INF/manifest.xml")
                if manifest_info.file_size <= _MAX_MANIFEST_BYTES:
                    manifest_bytes = archive.read(manifest_info)
                    if b"encryption-data" in manifest_bytes:
                        raise EncryptedFileError("Plik ODS jest zaszyfrowany lub chroniony hasłem.")

            if "mimetype" in names:
                mime_info = archive.getinfo("mimetype")
                if mime_info.file_size > 128 or archive.read(mime_info).strip() != _ODS_MIMETYPE:
                    raise CorruptedFileError(
                        "Plik ODS zawiera nieprawidłowy wpis mimetype w archiwum ODF."
                    )

            if "content.xml" not in names:
                raise CorruptedFileError("Plik ODS nie zawiera wymaganego wpisu content.xml.")
    except EncryptedFileError:
        raise
    except CorruptedFileError:
        raise
    except (zipfile.BadZipFile, KeyError, ValueError, OSError) as exc:
        raise CorruptedFileError("Plik ODS nie jest prawidłowym archiwum ZIP/ODF.") from exc


def extract_ods(ctx: ExtractionContext) -> ExtractedDocument:
    """Waliduje plik ODS i przygotowuje strumień w pamięci pod ekstrakcję (odfdo)."""
    _validate_ods_bytes(ctx.content)

    with io.BytesIO(ctx.content) as ods_stream:
        # Szkielet: właściwa ekstrakcja arkuszy i tabel (odfdo) będzie działać na ``ods_stream``.
        _ = ods_stream
        return build_skeleton_document(
            ctx,
            warnings=["Ekstraktor ODS działa w trybie szkieletowym."],
        )
