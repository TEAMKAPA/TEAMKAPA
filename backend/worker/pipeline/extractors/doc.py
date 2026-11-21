"""Ekstraktor starszych dokumentów Word (.doc / OLE2) z użyciem biblioteki olefile."""

import io
import re
import struct

import olefile

from backend.shared.schemas.pipeline import (
    ExtractedDocument,
    ExtractedPage,
    ExtractedTable,
    ExtractedTableCell,
    ExtractedTextBlock,
)
from backend.shared.schemas.quality import SourceChannel
from backend.worker.pipeline.extractors.base import ExtractionContext
from backend.worker.pipeline.extractors.exceptions import CorruptedFileError, EncryptedFileError

_FIB_WORD97_IDENT = 0xA5EC
_FIB_WORD6_IDENT = 0xA5DC
_FIB_FLAG_ENCRYPTED = 0x0100
_FIB_FLAG_WHICH_TBL_STM = 0x0200
_FIB_FLAG_OBFUSCATED = 0x1000
_FIB_CCP_TEXT_OFFSET = 0x004C
_FIB_CLX_ENTRY_INDEX = 33
_PIECE_COMPRESSED_FLAG = 0x40000000
_PIECE_FC_MASK = 0x3FFFFFFF

# Kody pól Worda: \x13 <kod pola> \x14 <tekst wyświetlany> \x15
_FIELD_WITH_RESULT_RE = re.compile(r"\x13[^\x14\x15]*\x14([^\x15]*)\x15")
_FIELD_NO_RESULT_RE = re.compile(r"\x13[^\x15]*\x15")
_POLISH_PRINTABLE_RE = re.compile(
    r"[A-Za-z0-9ĄĆĘŁŃÓŚŹŻąćęłńóśźż.,:;!?()/\-\"'„”%@#&+=\s\r\n\t\x07]"
)


def _extract_pieces_from_clx(
    word_stream: bytes,
    table_stream: bytes,
    *,
    fc_clx: int,
    lcb_clx: int,
) -> str | None:
    """Dekoduje tablicę kawałków MS-DOC (PlcPcd): UTF-16LE dla Unicode i cp1250 dla 8-bit."""
    if lcb_clx <= 0 or fc_clx < 0 or fc_clx + lcb_clx > len(table_stream):
        return None

    clx = table_stream[fc_clx : fc_clx + lcb_clx]
    pos = 0
    while pos < len(clx):
        clxt = clx[pos]
        pos += 1
        if clxt == 0x01:
            if pos + 2 > len(clx):
                return None
            cb_grpprl = struct.unpack_from("<H", clx, pos)[0]
            pos += 2 + cb_grpprl
        elif clxt == 0x02:
            if pos + 4 > len(clx):
                return None
            lcb_pcdt = struct.unpack_from("<I", clx, pos)[0]
            pos += 4
            if lcb_pcdt < 16 or pos + lcb_pcdt > len(clx):
                return None

            pcdt = clx[pos : pos + lcb_pcdt]
            if (lcb_pcdt - 4) % 12 != 0:
                return None
            piece_count = (lcb_pcdt - 4) // 12
            if piece_count <= 0:
                return None

            cps = struct.unpack_from(f"<{piece_count + 1}I", pcdt, 0)
            pcd_base = 4 * (piece_count + 1)
            chunks: list[str] = []

            for idx in range(piece_count):
                char_count = cps[idx + 1] - cps[idx]
                if char_count <= 0:
                    continue
                fc_raw = struct.unpack_from("<I", pcdt, pcd_base + idx * 8 + 2)[0]
                is_compressed = bool(fc_raw & _PIECE_COMPRESSED_FLAG)
                fc_val = fc_raw & _PIECE_FC_MASK

                if is_compressed:
                    byte_offset = fc_val // 2
                    byte_end = byte_offset + char_count
                    if byte_offset >= len(word_stream):
                        continue
                    raw_slice = word_stream[byte_offset : min(byte_end, len(word_stream))]
                    chunks.append(raw_slice.decode("cp1250", errors="replace"))
                else:
                    byte_offset = fc_val
                    byte_end = byte_offset + (char_count * 2)
                    if byte_offset >= len(word_stream):
                        continue
                    raw_slice = word_stream[byte_offset : min(byte_end, len(word_stream))]
                    even_len = len(raw_slice) - (len(raw_slice) % 2)
                    chunks.append(raw_slice[:even_len].decode("utf-16-le", errors="replace"))

            return "".join(chunks)
        else:
            break

    return None


def _read_fib_piece_text(ole: olefile.OleFileIO, word_stream: bytes) -> str | None:
    """Odczytuje nagłówek FIB ze strumienia WordDocument i dekoduje tekst przez 0Table/1Table."""
    if len(word_stream) < 0x22:
        return None

    w_ident = struct.unpack_from("<H", word_stream, 0)[0]
    if w_ident != _FIB_WORD97_IDENT:
        return None

    flags = struct.unpack_from("<H", word_stream, 0x0A)[0]
    table_name = "1Table" if (flags & _FIB_FLAG_WHICH_TBL_STM) else "0Table"
    if not ole.exists(table_name):
        return None

    # Dynamiczne wyznaczenie offsetu FibRgFcLcb (fcClx/lcbClx = wpis nr 33).
    csw = struct.unpack_from("<H", word_stream, 0x20)[0]
    cslw_off = 0x22 + (2 * csw)
    if cslw_off + 2 > len(word_stream):
        return None
    cslw = struct.unpack_from("<H", word_stream, cslw_off)[0]

    cb_rg_fc_lcb_off = cslw_off + 2 + (4 * cslw)
    if cb_rg_fc_lcb_off + 2 > len(word_stream):
        return None
    cb_rg_fc_lcb = struct.unpack_from("<H", word_stream, cb_rg_fc_lcb_off)[0]
    if cb_rg_fc_lcb <= _FIB_CLX_ENTRY_INDEX:
        return None

    clx_pair_off = cb_rg_fc_lcb_off + 2 + (_FIB_CLX_ENTRY_INDEX * 8)
    if clx_pair_off + 8 > len(word_stream):
        return None

    fc_clx, lcb_clx = struct.unpack_from("<II", word_stream, clx_pair_off)
    table_stream = ole.openstream(table_name).read()
    decoded = _extract_pieces_from_clx(word_stream, table_stream, fc_clx=fc_clx, lcb_clx=lcb_clx)
    if decoded is None:
        return None

    if len(word_stream) >= _FIB_CCP_TEXT_OFFSET + 4:
        ccp_text = struct.unpack_from("<I", word_stream, _FIB_CCP_TEXT_OFFSET)[0]
        if 0 < ccp_text < len(decoded):
            return decoded[:ccp_text]
    return decoded


def _is_readable_char(ch: str) -> bool:
    return bool(_POLISH_PRINTABLE_RE.fullmatch(ch))


def _extract_readable_runs(text: str, *, min_len: int = 4) -> list[str]:
    runs: list[str] = []
    current: list[str] = []
    for ch in text:
        if _is_readable_char(ch):
            current.append(ch)
        else:
            if len(current) >= min_len:
                segment = "".join(current).strip()
                if len(segment) >= min_len and any(c.isalpha() for c in segment):
                    runs.append(segment)
            current = []
    if len(current) >= min_len:
        segment = "".join(current).strip()
        if len(segment) >= min_len and any(c.isalpha() for c in segment):
            runs.append(segment)
    return runs


def _fallback_decode_word_stream(word_stream: bytes) -> tuple[str, str]:
    """Awaryjne dekodowanie UTF-16LE / cp1250 bezpośrednio ze strumienia WordDocument."""
    # Pomiń nagłówek FIB (jeśli obecny), by uniknąć losowych bajtów nagłówka.
    payload = word_stream
    if len(word_stream) >= 0x200 and struct.unpack_from("<H", word_stream, 0)[0] in {
        _FIB_WORD97_IDENT,
        _FIB_WORD6_IDENT,
    }:
        payload = word_stream[0x200:]

    even_len = len(payload) - (len(payload) % 2)
    utf16_text = payload[:even_len].decode("utf-16-le", errors="ignore")
    cp1250_text = payload.decode("cp1250", errors="ignore")

    utf16_runs = _extract_readable_runs(utf16_text)
    cp1250_runs = _extract_readable_runs(cp1250_text)

    utf16_score = sum(sum(1 for c in r if c.isalpha()) for r in utf16_runs)
    cp1250_score = sum(sum(1 for c in r if c.isalpha()) for r in cp1250_runs)

    if utf16_score >= cp1250_score and utf16_score > 0:
        return "\n".join(utf16_runs), "utf-16-le"
    if cp1250_score > 0:
        return "\n".join(cp1250_runs), "cp1250"
    return "", "none"


def _strip_word_controls(raw: str) -> str:
    text = _FIELD_WITH_RESULT_RE.sub(r"\1", raw)
    text = _FIELD_NO_RESULT_RE.sub("", text)
    text = (
        text.replace("\r\n", "\n").replace("\r", "\n").replace("\x0b", "\n").replace("\x0c", "\n")
    )
    # Usuń pozostałe znaki sterujące oprócz \n, \t i \x07 (znacznik komórek tabeli Word).
    return "".join(ch for ch in text if ch in {"\n", "\t", "\x07"} or ord(ch) >= 32)


def _split_paragraphs_and_tables(
    cleaned: str,
) -> tuple[list[ExtractedTextBlock], list[ExtractedTable]]:
    """Rozdziela akapity i tabele (znaczniki ``\\x07``) w strumieniu tekstowym MS-DOC."""
    blocks: list[ExtractedTextBlock] = []
    tables: list[ExtractedTable] = []
    current_table_rows: list[list[str]] = []

    def _flush_table() -> None:
        if not current_table_rows:
            return
        max_cols = max(len(r) for r in current_table_rows)
        padded = [r + [""] * (max_cols - len(r)) for r in current_table_rows]
        cells = [
            ExtractedTableCell(row=r_idx, col=c_idx, text=val)
            for r_idx, row in enumerate(padded)
            for c_idx, val in enumerate(row)
        ]
        tables.append(ExtractedTable(page=1, rows=padded, cells=cells))
        current_table_rows.clear()

    for line in cleaned.split("\n"):
        if "\x07" in line:
            parts = [p.strip() for p in line.split("\x07")]
            # W MS-DOC wiersz kończy się podwójnym \x07\x07 — usuń końcowe puste elementy.
            while parts and parts[-1] == "":
                parts.pop()
            if parts:
                current_table_rows.append(parts)
            continue

        _flush_table()
        stripped = line.strip()
        if stripped:
            blocks.append(ExtractedTextBlock(text=stripped, page=1, channel=SourceChannel.TEXT))

    _flush_table()
    return blocks, tables


def _table_to_markdown(table: ExtractedTable) -> str:
    if not table.rows:
        return ""
    col_count = max(len(r) for r in table.rows)
    if col_count == 0:
        return ""

    def _esc(val: str) -> str:
        return val.replace("|", "\\|").replace("\n", " ").strip()

    lines = [
        "| " + " | ".join(_esc(c) for c in table.rows[0]) + " |",
        "| " + " | ".join("---" for _ in range(col_count)) + " |",
    ]
    for row in table.rows[1:]:
        lines.append("| " + " | ".join(_esc(c) for c in row) + " |")
    return "\n".join(lines)


def extract_doc(ctx: ExtractionContext) -> ExtractedDocument:
    """Parsuje binarny plik .doc (OLE2) przez olefile: WordDocument (UTF-16LE / cp1250)."""
    if not olefile.isOleFile(io.BytesIO(ctx.content)):
        raise CorruptedFileError("Plik DOC nie jest prawidłowym kontenerem OLE2.")

    try:
        with olefile.OleFileIO(io.BytesIO(ctx.content)) as ole:
            if ole.exists("EncryptedPackage") or ole.exists("EncryptionInfo"):
                raise EncryptedFileError("Plik DOC jest zaszyfrowany lub chroniony hasłem.")
            if not ole.exists("WordDocument"):
                raise CorruptedFileError("Kontener OLE2 nie zawiera strumienia WordDocument.")

            word_stream = ole.openstream("WordDocument").read()
            if len(word_stream) < 12:
                raise CorruptedFileError("Strumień WordDocument jest uszkodzony lub ucięty.")

            w_ident = struct.unpack_from("<H", word_stream, 0)[0]
            if w_ident in {_FIB_WORD97_IDENT, _FIB_WORD6_IDENT}:
                flags = struct.unpack_from("<H", word_stream, 0x0A)[0]
                if flags & (_FIB_FLAG_ENCRYPTED | _FIB_FLAG_OBFUSCATED):
                    raise EncryptedFileError(
                        "Plik DOC jest chroniony hasłem (flaga FIB fEncrypted)."
                    )

            raw_text = _read_fib_piece_text(ole, word_stream)
            decode_strategy = "piece_table"
            if raw_text is None:
                raw_text, decode_strategy = _fallback_decode_word_stream(word_stream)
    except EncryptedFileError:
        raise
    except CorruptedFileError:
        raise
    except (olefile.NotOleFileError, struct.error, ValueError, OSError) as exc:
        raise CorruptedFileError(f"Nie udało się odczytać pliku DOC (OLE2): {exc}") from exc

    cleaned = _strip_word_controls(raw_text)
    blocks, tables = _split_paragraphs_and_tables(cleaned)

    text_parts = [b.text for b in blocks]
    for table in tables:
        text_parts.extend(" | ".join(row) for row in table.rows if any(c.strip() for c in row))
    full_text = "\n".join(text_parts)

    md_parts = [b.text for b in blocks]
    md_parts.extend(_table_to_markdown(t) for t in tables if t.rows)
    markdown = "\n\n".join(p for p in md_parts if p)

    channels = [SourceChannel.TEXT] if (blocks or tables) else []
    page = ExtractedPage(
        page_number=1,
        text=full_text,
        blocks=blocks,
        tables=tables,
        source_channels=channels,
    )

    warnings: list[str] = []
    if decode_strategy != "piece_table":
        warnings.append(
            "Brak pełnej tablicy kawałków (0Table/1Table) — użyto awaryjnego dekodowania "
            f"strumienia WordDocument ({decode_strategy})."
        )

    return ExtractedDocument(
        document_sha256=ctx.sha256,
        filename=ctx.filename,
        extension=ctx.extension,
        mime_type=ctx.mime_type,
        document_format=ctx.document_format,
        page_count=1,
        full_text=full_text,
        markdown=markdown,
        pages=[page],
        tables=tables,
        source_channels=channels,
        warnings=warnings,
        metadata={"extractor": "olefile", "decode_strategy": decode_strategy},
    )
