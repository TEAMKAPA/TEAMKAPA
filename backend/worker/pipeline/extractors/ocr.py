"""Preprocessing OpenCV (skala szarości, prostowanie kąta, binaryzacja) i OCR Tesseract."""

from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np
import pytesseract

from backend.shared.schemas.pipeline import ExtractedTextBlock, OcrWord
from backend.shared.schemas.quality import BoundingBox, SourceChannel
from backend.worker.pipeline.extractors.exceptions import CorruptedFileError, ExtractionError

_MIN_DESKEW_DEGREES = 0.25
_MAX_DESKEW_DEGREES = 15.0
_MIN_FOREGROUND_PIXELS = 50
_ADAPTIVE_BLOCK_SIZE = 31
_ADAPTIVE_C = 11


@dataclass(frozen=True, slots=True)
class OcrResult:
    """Wynik pojedynczego przebiegu OCR dla jednej strony lub obrazu."""

    text: str
    words: list[OcrWord]
    blocks: list[ExtractedTextBlock]
    mean_confidence: float | None


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def estimate_skew_angle(gray: np.ndarray[Any, Any]) -> float:
    """Wyznacza kąt korekty (w stopniach CCW) potrzebny do wyprostowania przekrzywionego skanu."""
    _, binary_inv = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    ys, xs = np.where(binary_inv > 0)
    if len(xs) < _MIN_FOREGROUND_PIXELS:
        return 0.0

    coords = np.column_stack((xs, ys)).astype(np.float32)
    (_, _), _, raw_angle = cv2.minAreaRect(coords)
    # Normalizacja wspólna dla konwencji OpenCV 4.x (0, 90] oraz 5.x [-90, 0).
    correction = ((float(raw_angle) + 45.0) % 90.0) - 45.0

    if abs(correction) < _MIN_DESKEW_DEGREES or abs(correction) > _MAX_DESKEW_DEGREES:
        return 0.0
    return correction


def deskew_grayscale(gray: np.ndarray[Any, Any]) -> tuple[np.ndarray[Any, Any], float]:
    """Prostuje obraz w skali szarości i zwraca ``(obraz_po_korekcie, kąt_korekty)``."""
    angle = estimate_skew_angle(gray)
    if angle == 0.0:
        return gray, 0.0

    height, width = gray.shape[:2]
    center = (width / 2.0, height / 2.0)
    matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
    rotated = cv2.warpAffine(
        gray,
        matrix,
        (width, height),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=255,
    )
    return rotated, angle


def preprocess_image_for_ocr(image_bytes: bytes) -> tuple[np.ndarray[Any, Any], int, int]:
    """Przetwarza obraz: dekodowanie -> skala szarości -> prostowanie kąta -> binaryzacja."""
    buffer = np.frombuffer(image_bytes, dtype=np.uint8)
    decoded = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
    if decoded is None or decoded.size == 0:
        raise CorruptedFileError("Nie udało się zdekodować obrazu podczas preprocessingu OpenCV.")

    gray = cv2.cvtColor(decoded, cv2.COLOR_BGR2GRAY)
    height, width = gray.shape[:2]
    deskewed, _ = deskew_grayscale(gray)

    block_size = min(_ADAPTIVE_BLOCK_SIZE, max(3, min(height, width) | 1))
    if block_size % 2 == 0:
        block_size = max(3, block_size - 1)

    binarized = cv2.adaptiveThreshold(
        deskewed,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        block_size,
        _ADAPTIVE_C,
    )
    return binarized, width, height


def _bbox_from_pixels(
    left: int,
    top: int,
    box_w: int,
    box_h: int,
    img_w: int,
    img_h: int,
) -> BoundingBox | None:
    if img_w <= 0 or img_h <= 0 or box_w <= 0 or box_h <= 0:
        return None
    x0 = _clamp01(left / img_w)
    y0 = _clamp01(top / img_h)
    x1 = _clamp01((left + box_w) / img_w)
    y1 = _clamp01((top + box_h) / img_h)
    if x1 < x0 or y1 < y0:
        return None
    return BoundingBox(x0=x0, y0=y0, x1=x1, y1=y1)


def _union_bboxes(bboxes: list[BoundingBox]) -> BoundingBox | None:
    if not bboxes:
        return None
    return BoundingBox(
        x0=min(b.x0 for b in bboxes),
        y0=min(b.y0 for b in bboxes),
        x1=max(b.x1 for b in bboxes),
        y1=max(b.y1 for b in bboxes),
    )


def _parse_tesseract_data(
    data: dict[str, list[Any]],
    *,
    page_number: int,
    img_w: int,
    img_h: int,
) -> OcrResult:
    texts = data.get("text", [])
    confs = data.get("conf", [])
    lefts = data.get("left", [])
    tops = data.get("top", [])
    widths = data.get("width", [])
    heights = data.get("height", [])
    block_nums = data.get("block_num", [])
    par_nums = data.get("par_num", [])
    line_nums = data.get("line_num", [])

    words: list[OcrWord] = []
    grouped_lines: dict[tuple[int, int, int], list[OcrWord]] = {}

    count = len(texts)
    for idx in range(count):
        raw_text = str(texts[idx]).strip()
        if not raw_text:
            continue
        try:
            raw_conf = float(confs[idx])
        except TypeError, ValueError:
            continue
        if raw_conf < 0.0:
            continue

        confidence = _clamp01(raw_conf / 100.0)
        bbox = _bbox_from_pixels(
            int(lefts[idx]),
            int(tops[idx]),
            int(widths[idx]),
            int(heights[idx]),
            img_w,
            img_h,
        )
        word = OcrWord(text=raw_text, confidence=confidence, bbox=bbox)
        words.append(word)

        line_key = (
            int(block_nums[idx]) if idx < len(block_nums) else 0,
            int(par_nums[idx]) if idx < len(par_nums) else 0,
            int(line_nums[idx]) if idx < len(line_nums) else 0,
        )
        grouped_lines.setdefault(line_key, []).append(word)

    blocks: list[ExtractedTextBlock] = []
    line_texts: list[str] = []
    for line_words in grouped_lines.values():
        line_text = " ".join(w.text for w in line_words).strip()
        if not line_text:
            continue
        line_texts.append(line_text)
        line_bboxes = [w.bbox for w in line_words if w.bbox is not None]
        line_conf = sum(w.confidence for w in line_words) / len(line_words)
        blocks.append(
            ExtractedTextBlock(
                text=line_text,
                page=page_number,
                bbox=_union_bboxes(line_bboxes),
                channel=SourceChannel.OCR,
                confidence=_clamp01(line_conf),
            )
        )

    mean_conf = sum(w.confidence for w in words) / len(words) if words else None
    return OcrResult(
        text="\n".join(line_texts),
        words=words,
        blocks=blocks,
        mean_confidence=_clamp01(mean_conf) if mean_conf is not None else None,
    )


def run_tesseract_ocr(
    image_bytes: bytes,
    *,
    page_number: int,
    lang: str = "pol",
) -> OcrResult:
    """Wykonuje preprocessing OpenCV oraz OCR Tesseract z zapisem pewności słów."""
    processed, img_w, img_h = preprocess_image_for_ocr(image_bytes)
    try:
        raw_data: dict[str, list[Any]] = pytesseract.image_to_data(
            processed,
            lang=lang,
            output_type=pytesseract.Output.DICT,
            config="--oem 3 --psm 3",
        )
    except (pytesseract.TesseractNotFoundError, pytesseract.TesseractError, OSError) as exc:
        raise ExtractionError(
            f"Błąd silnika OCR Tesseract (lang={lang}): {exc}",
            code="OCR_ENGINE_ERROR",
        ) from exc

    return _parse_tesseract_data(raw_data, page_number=page_number, img_w=img_w, img_h=img_h)
