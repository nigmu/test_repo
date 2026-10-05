"""Encoding detection and transcoding (DESIGN.md section 4)."""

from __future__ import annotations

import codecs
from pathlib import Path

from .model import EncodingInfo
from .settings import Settings

# cp1252 leaves these bytes undefined; map them via Latin-1.
_CP1252_UNDEFINED = {0x81, 0x8D, 0x8F, 0x90, 0x9D}

_HANDLER_NAME = "csv_comparator_cp1252_fallback"


def _byte_to_fallback_char(byte: int) -> str:
    if byte in _CP1252_UNDEFINED:
        return chr(byte)
    try:
        return bytes([byte]).decode("cp1252")
    except UnicodeDecodeError:
        return chr(byte)


def _transcode_utf16(src: Path, dest: Path, encoding: str, chunk_mb: int) -> None:
    chunk = chunk_mb * 1024 * 1024
    with src.open("rb") as fin, dest.open("w", encoding="utf-8", newline="") as fout:
        decoder = codecs.getincrementaldecoder(encoding)("strict")
        while True:
            data = fin.read(chunk)
            if not data:
                break
            try:
                text = decoder.decode(data, final=False)
            except UnicodeDecodeError as e:
                raise RuntimeError(f"UTF-16 decode error in {src.name}: {e}") from e
            fout.write(text)
        try:
            text = decoder.decode(b"", final=True)
        except UnicodeDecodeError as e:
            raise RuntimeError(f"UTF-16 decode error in {src.name}: {e}") from e
        if text:
            fout.write(text)


def _nul_pattern_utf16(sample: bytes) -> str | None:
    """Detect UTF-16 without BOM from NUL bytes in every other position."""
    if len(sample) < 4:
        return None
    n = len(sample) - (len(sample) % 2)
    if n < 4:
        return None
    even_nuls = sum(1 for i in range(0, n, 2) if sample[i] == 0)
    odd_nuls = sum(1 for i in range(1, n, 2) if sample[i] == 0)
    half = n // 2
    if odd_nuls >= half * 0.9 and even_nuls < half * 0.1:
        return "utf-16-le"
    if even_nuls >= half * 0.9 and odd_nuls < half * 0.1:
        return "utf-16-be"
    return None


def _check_utf8_streaming(path: Path, chunk_mb: int) -> bool:
    """Return True if the whole file is valid UTF-8."""
    chunk = chunk_mb * 1024 * 1024
    decoder = codecs.getincrementaldecoder("utf-8")("strict")
    with path.open("rb") as f:
        try:
            while True:
                data = f.read(chunk)
                if not data:
                    break
                decoder.decode(data, final=False)
            decoder.decode(b"", final=True)
        except UnicodeDecodeError:
            return False
    return True


def _utf8_seq_len(lead: int) -> int:
    if lead & 0x80 == 0:
        return 1
    if lead & 0xE0 == 0xC0:
        return 2
    if lead & 0xF0 == 0xE0:
        return 3
    if lead & 0xF8 == 0xF0:
        return 4
    return 0


def _decode_mixed_chunk(
    buf: bytes,
    *,
    final: bool,
    line_no: int,
    fallback_count: int,
    fallback_lines: list[int],
) -> tuple[str, int, int, int, list[int]]:
    """Decode mixed UTF-8/cp1252 buffer. Returns text, bytes_consumed, new_line_no, count, lines."""
    out: list[str] = []
    i = 0
    n = len(buf)
    count = fallback_count
    lines = list(fallback_lines)

    def note_fallback() -> None:
        nonlocal count
        count += 1
        effective = line_no + "".join(out).count("\n")
        if len(lines) < 10 and (not lines or lines[-1] != effective):
            lines.append(effective)

    while i < n:
        if not final:
            need = _utf8_seq_len(buf[i])
            if need == 0:
                out.append(_byte_to_fallback_char(buf[i]))
                note_fallback()
                i += 1
                continue
            if i + need > n:
                break  # hold incomplete sequence

        remaining = buf[i:]
        try:
            text = remaining.decode("utf-8")
            out.append(text)
            i = n
            break
        except UnicodeDecodeError as e:
            if e.start > 0:
                out.append(remaining[: e.start].decode("utf-8"))
                i += e.start
                continue
            for j in range(e.start, e.end):
                out.append(_byte_to_fallback_char(remaining[j]))
                note_fallback()
            i += e.end - e.start

    text = "".join(out)
    new_line_no = line_no + text.count("\n")
    return text, i, new_line_no, count, lines


def _transcode_mixed(src: Path, dest: Path, chunk_mb: int) -> tuple[int, list[int]]:
    chunk = chunk_mb * 1024 * 1024
    line_no = 1
    count = 0
    lines: list[int] = []

    with src.open("rb") as fin, dest.open("w", encoding="utf-8", newline="") as fout:
        buf = b""
        while True:
            piece = fin.read(chunk)
            final = not piece
            buf += piece
            if not buf and final:
                break
            text, consumed, line_no, count, lines = _decode_mixed_chunk(
                buf,
                final=final,
                line_no=line_no,
                fallback_count=count,
                fallback_lines=lines,
            )
            if text:
                fout.write(text)
            buf = buf[consumed:]
            if final:
                break

    return count, lines


def prepare_encoding(
    path: Path,
    temp_folder: Path,
    settings: Settings,
    side: str,
    prefix: str,
) -> EncodingInfo:
    """Detect encoding and return a UTF-8 path DuckDB can read."""
    chunk_mb = settings.read_chunk_mb
    temp_folder.mkdir(parents=True, exist_ok=True)

    with path.open("rb") as f:
        head = f.read(4096)

    if head.startswith(b"\xff\xfe"):
        dest = temp_folder / f"{prefix}_{side}_utf8.csv"
        _transcode_utf16(path, dest, "utf-16-le", chunk_mb)
        return EncodingInfo(label="UTF-16LE (BOM)", path_for_reader=dest, is_temp=True)

    if head.startswith(b"\xfe\xff"):
        dest = temp_folder / f"{prefix}_{side}_utf8.csv"
        _transcode_utf16(path, dest, "utf-16-be", chunk_mb)
        return EncodingInfo(label="UTF-16BE (BOM)", path_for_reader=dest, is_temp=True)

    has_utf8_bom = head.startswith(b"\xef\xbb\xbf")

    if not has_utf8_bom:
        enc = _nul_pattern_utf16(head)
        if enc is not None:
            dest = temp_folder / f"{prefix}_{side}_utf8.csv"
            _transcode_utf16(path, dest, enc, chunk_mb)
            label = (
                "UTF-16LE (no BOM, detected from NUL pattern)"
                if enc == "utf-16-le"
                else "UTF-16BE (no BOM, detected from NUL pattern)"
            )
            return EncodingInfo(label=label, path_for_reader=dest, is_temp=True)

    if _check_utf8_streaming(path, chunk_mb):
        label = "UTF-8 (BOM)" if has_utf8_bom else "UTF-8"
        return EncodingInfo(label=label, path_for_reader=path, is_temp=False)

    dest = temp_folder / f"{prefix}_{side}_utf8.csv"
    count, lines = _transcode_mixed(path, dest, chunk_mb)
    lines_str = ", ".join(str(x) for x in lines)
    label = f"UTF-8 with {count} bytes decoded as cp1252 (first at lines {lines_str})"
    warning = (
        f"{path.name}: {count} bytes decoded as cp1252 (first at lines {lines_str})"
    )
    return EncodingInfo(
        label=label,
        path_for_reader=dest,
        is_temp=True,
        fallback_bytes=count,
        fallback_lines=lines,
        warning=warning,
    )
