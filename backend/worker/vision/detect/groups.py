"""Kandydaci na grupy wyboru (C.6).

OpenCV nie zna treści pytań, więc grupy są tylko **kandydatami** z niską pewnością
(``GROUP_CANDIDATE``) i ``multi`` nieznanym (``None``); rozstrzyga VLM i fuzja (Etapy D i E).

1. Wiersze: checkboxy o środkach w pionie w granicach ``group_row_tolerance_mm``, kolejne nie dalej
   niż ``group_row_max_gap_mm``; ≥ 2 checkboxy tworzą grupę.
2. Kolumny: checkboxy spoza grup wierszowych, lewe krawędzie w granicach ``group_row_tolerance_mm``,
   kolejne nie dalej niż ``group_column_max_gap_mm`` w pionie.
3. Ramka grupy obejmuje checkboxy i tekst opcji na prawo od każdego z nich (tusz bez cienkich linii
   i bez linii kropkowanych, w pasie wysokości checkboxa), aż do przerwy ``group_text_gap_mm``,
   linii pionowej, następnego checkboxa albo ``group_text_max_mm`` (konwencja ``LABELING.md``:
   grupa obejmuje teksty opcji).

Role elementów OpenCV pozostają ``unknown``.
"""

from collections.abc import Sequence

from backend.worker.vision.config import DetectConfig
from backend.worker.vision.detect.candidates import Candidate
from backend.worker.vision.detect.lines import LineMaps
from backend.worker.vision.detect.preprocess import Mask, PixelBox
from backend.worker.vision.detect.scale import Scale
from backend.worker.vision.models import ElementKind

GROUP_CANDIDATE = "GROUP_CANDIDATE"
GROUP_CONFIDENCE = 0.3


def _cy(box: PixelBox) -> float:
    return (box[1] + box[3]) / 2


def _row_chains(boxes: list[PixelBox], tolerance: float, max_gap: float) -> list[list[PixelBox]]:
    rows: list[list[PixelBox]] = []
    for box in sorted(boxes, key=lambda b: (_cy(b), b[0])):
        if rows and abs(_cy(box) - _cy(rows[-1][0])) <= tolerance:
            rows[-1].append(box)
        else:
            rows.append([box])
    chains: list[list[PixelBox]] = []
    for row in rows:
        current: list[PixelBox] = []
        for box in sorted(row, key=lambda b: b[0]):
            if current and box[0] - current[-1][2] > max_gap:
                chains.append(current)
                current = []
            current.append(box)
        chains.append(current)
    return chains


def _column_chains(boxes: list[PixelBox], tolerance: float, max_gap: float) -> list[list[PixelBox]]:
    columns: list[list[PixelBox]] = []
    for box in sorted(boxes, key=lambda b: (b[0], b[1])):
        if columns and abs(box[0] - columns[-1][0][0]) <= tolerance:
            columns[-1].append(box)
        else:
            columns.append([box])
    chains: list[list[PixelBox]] = []
    for column in columns:
        current: list[PixelBox] = []
        for box in sorted(column, key=lambda b: b[1]):
            if current and box[1] - current[-1][3] > max_gap:
                chains.append(current)
                current = []
            current.append(box)
        chains.append(current)
    return chains


def _text_end(
    text: Mask, vertical: Mask, box: PixelBox, stop: int, cfg: DetectConfig, scale: Scale
) -> int:
    """Prawa krawędź tekstu opcji zaczynającego się na prawo od checkboxa (albo ``box[2]``)."""
    height, width = text.shape
    pad = (box[3] - box[1]) // 4
    y0, y1 = max(box[1] - pad, 0), min(box[3] + pad, height)
    gap_limit = scale.ipx(cfg.group_text_gap_mm)
    limit = min(stop, width, box[2] + scale.ipx(cfg.group_text_max_mm))
    end, gap = box[2], 0
    for x in range(box[2], limit):
        if vertical[y0:y1, x].any():
            break
        if text[y0:y1, x].any():
            end, gap = x + 1, 0
        else:
            gap += 1
            if gap > gap_limit and end > box[2]:
                break
            if gap > gap_limit * 2:
                break
    return end


def _text_mask(
    ink: Mask, lines: LineMaps, exclude: Sequence[PixelBox], cfg: DetectConfig, scale: Scale
) -> Mask:
    """Tusz tekstu: bez cienkich linii (obramowania, podkreślenia) i bez ``exclude`` (kropki)."""
    text = ink.copy()
    thin = scale.px(cfg.border_max_mm)
    for segment in lines.h_segments + lines.v_segments:
        if segment.thickness <= thin:
            x0, y0, x1, y1 = segment.box
            text[y0:y1, x0:x1] = False
    for x0, y0, x1, y1 in exclude:
        text[y0:y1, x0:x1] = False
    return text


def checkbox_groups(
    checkboxes: Sequence[Candidate],
    ink: Mask,
    lines: LineMaps,
    cfg: DetectConfig,
    scale: Scale,
    exclude: Sequence[PixelBox] = (),
) -> list[Candidate]:
    """Kandydaci na grupy z checkboxów; ``exclude``: ramki niebędące tekstem (np. kropki)."""
    boxes = [c.box for c in checkboxes if c.kind is ElementKind.CHECKBOX]
    tolerance = scale.px(cfg.group_row_tolerance_mm)
    rows = [
        chain
        for chain in _row_chains(boxes, tolerance, scale.px(cfg.group_row_max_gap_mm))
        if len(chain) >= 2
    ]
    in_rows = {box for chain in rows for box in chain}
    columns = [
        chain
        for chain in _column_chains(
            [b for b in boxes if b not in in_rows], tolerance, scale.px(cfg.group_column_max_gap_mm)
        )
        if len(chain) >= 2
    ]
    text = _text_mask(ink, lines, exclude, cfg, scale)
    width = ink.shape[1]
    result = []
    for chain, is_row in [(c, True) for c in rows] + [(c, False) for c in columns]:
        ends = []
        for index, box in enumerate(chain):
            stop = chain[index + 1][0] if is_row and index + 1 < len(chain) else width
            ends.append(_text_end(text, lines.vertical, box, stop, cfg, scale))
        group = (
            min(b[0] for b in chain),
            min(b[1] for b in chain),
            max([b[2] for b in chain] + ends),
            max(b[3] for b in chain),
        )
        result.append(
            Candidate(
                kind=ElementKind.CHECKBOX_GROUP,
                box=group,
                confidence=GROUP_CONFIDENCE,
                reasons=(GROUP_CANDIDATE,),
                members=tuple(chain),
            )
        )
    return sorted(result, key=lambda c: (c.box[1], c.box[0]))
