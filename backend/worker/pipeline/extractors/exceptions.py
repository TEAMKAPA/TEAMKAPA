"""Wyjątki domenowe etapu ekstrakcji dokumentów (krok 02)."""


class ExtractionError(RuntimeError):
    """Bazowy wyjątek domenowy dla błędów ekstrakcji dokumentu."""

    default_code = "EXTRACTION_ERROR"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        details: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code or self.default_code
        self.message = message
        self.details = details or {}


class CorruptedFileError(ExtractionError):
    """Plik jest pusty, ucięty lub ma uszkodzoną strukturę wewnętrzną."""

    default_code = "FILE_CORRUPTED"


class EncryptedFileError(ExtractionError):
    """Plik jest zaszyfrowany lub chroniony hasłem i nie można go odczytać."""

    default_code = "FILE_ENCRYPTED"


class UnsupportedFileFormatError(ExtractionError):
    """Format lub typ MIME pliku nie jest obsługiwany albo jest niespójny z rozszerzeniem."""

    default_code = "UNSUPPORTED_FORMAT"


class FileTooLargeError(ExtractionError):
    """Rozmiar pliku przekracza skonfigurowany limit MAX_FILE_SIZE_MB."""

    default_code = "FILE_TOO_LARGE"
