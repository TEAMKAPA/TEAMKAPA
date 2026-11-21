"""Stałe kody błędów i ostrzeżeń kanału wizualnego.

Kody są częścią kontraktu (raport przeglądu, logi, testy), więc ich wartości się nie zmieniają.
Komunikaty i szczegóły nigdy nie zawierają treści dokumentu: tylko liczby, rodzaje i kody.
"""

from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # tylko typy: models importuje kody z tego modułu
    from backend.worker.vision.models import RenderIssue


class IssueCode(StrEnum):
    # --- błędy dokumentu (przerywają renderowanie dokumentu) ---
    UNSUPPORTED_FORMAT = "UNSUPPORTED_FORMAT"
    PDF_UNREADABLE = "PDF_UNREADABLE"
    PDF_ENCRYPTED = "PDF_ENCRYPTED"
    IMAGE_UNREADABLE = "IMAGE_UNREADABLE"
    TOO_MANY_PAGES = "TOO_MANY_PAGES"
    IMAGE_TOO_LARGE = "IMAGE_TOO_LARGE"
    PAGE_RENDER_TIMEOUT = "PAGE_RENDER_TIMEOUT"
    PAGE_RENDER_CRASHED = "PAGE_RENDER_CRASHED"
    OFFICE_CONVERSION_DISABLED = "OFFICE_CONVERSION_DISABLED"
    OFFICE_CONVERSION_FAILED = "OFFICE_CONVERSION_FAILED"
    OFFICE_CONVERSION_TIMEOUT = "OFFICE_CONVERSION_TIMEOUT"

    # --- ostrzeżenia i informacje (zapisywane w issues strony lub dokumentu) ---
    DPI_REDUCED = "DPI_REDUCED"
    DPI_UNKNOWN = "DPI_UNKNOWN"
    MULTI_FRAME_IMAGE = "MULTI_FRAME_IMAGE"
    IMAGE_SPLIT_A4 = "IMAGE_SPLIT_A4"
    TALL_IMAGE_UNSPLIT = "TALL_IMAGE_UNSPLIT"
    DECLARED_KIND_MISMATCH = "DECLARED_KIND_MISMATCH"


class RenderError(Exception):
    """Błąd dokumentu z kodem. ``detail``: wyłącznie dane techniczne, bez treści dokumentu.

    ``issues``: problemy dokumentu zebrane przed błędem (np. ``DECLARED_KIND_MISMATCH``), żeby
    nie zginęły w raporcie przeglądu, gdy renderowanie się nie powiedzie.
    """

    def __init__(
        self, code: IssueCode, detail: str = "", issues: tuple[RenderIssue, ...] = ()
    ) -> None:
        self.code = code
        self.detail = detail
        self.issues = issues
        super().__init__(f"{code}: {detail}" if detail else str(code))


class CoordinateError(ValueError):
    """Współrzędne poza stroną lub niepoprawne parametry przeliczenia."""
