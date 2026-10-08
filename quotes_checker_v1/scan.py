"""Stream a CSV and count how values and empty values are quoted.

Quoting is a habit of the writer, but it is not safe to judge that habit
from the first rows. Minimal quoting adds quotes only when a value contains
the delimiter, a quote, or a line break. Quoted empties ("" ) can be rare.
This module reads every row, keeps running totals, and drops the row.
"""

from __future__ import annotations

import codecs
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
import re


_NUM_STR = "+-0123456789.eE"
_NUM_RE = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?\Z")

# Parser states. START is the beginning of a field.
_START = "start"
_UNQUOTED = "unquoted"
_QUOTED = "quoted"
_CLOSED = "closed"

_COUNTERS = (
    "fields",
    "empty_quoted",
    "empty_bare",
    "ws_quoted",
    "ws_bare",
    "quoted_required",
    "quoted_optional",
    "bare",
    "bare_with_quote",
    "numeric_quoted",
    "numeric_bare",
    "text_quoted",
    "text_bare",
    "with_delim",
    "with_break",
    "with_inner_quote",
)

_PREFIX_BYTES = 512 * 1024
_SNIFF_ROWS = 200
_CHUNK_BYTES = 1024 * 1024
_SAMPLE_CHARS = 40


@dataclass
class Bucket:
    """Counts for one part of a file (the header, or the data rows)."""

    fields: int = 0
    empty_quoted: int = 0
    empty_bare: int = 0
    ws_quoted: int = 0
    ws_bare: int = 0
    quoted_required: int = 0
    quoted_optional: int = 0
    bare: int = 0
    bare_with_quote: int = 0
    numeric_quoted: int = 0
    numeric_bare: int = 0
    text_quoted: int = 0
    text_bare: int = 0
    with_delim: int = 0
    with_break: int = 0
    with_inner_quote: int = 0

    def absorb(self, other: Bucket) -> None:
        for name in _COUNTERS:
            setattr(self, name, getattr(self, name) + getattr(other, name))

    def reset(self) -> None:
        for name in _COUNTERS:
            setattr(self, name, 0)

    def has_quotes(self) -> bool:
        return (
            self.empty_quoted
            + self.quoted_optional
            + self.quoted_required
            + self.ws_quoted
        ) > 0


@dataclass(frozen=True)
class Sample:
    row: int
    text: str


@dataclass
class Dialect:
    delim: str
    quote: str
    delim_status: str  # detected, given, guessed
    quote_status: str  # detected, given


@dataclass
class Scan:
    path: Path
    error: str | None = None
    size: int = 0
    elapsed: float = 0.0
    encoding: str = ""
    encoding_note: str = ""
    delim: str = ","
    delim_status: str = "detected"
    quote: str = '"'
    quote_assumed: bool = False
    newline_label: str = "none"
    columns: int = 0
    header: Bucket = field(default_factory=Bucket)
    data: Bucket = field(default_factory=Bucket)
    header_records: int = 0
    data_records: int = 0
    ragged: int = 0
    blank_lines: int = 0
    unterminated: int = 0
    first_unterminated: int | None = None
    junk_after_quote: int = 0
    padded_quotes: int = 0
    low_confidence: bool = False
    hit_max: bool = False
    max_rows: int = 0
    expect_header: bool = True
    judged: str = "none"  # data, header, none
    header_samples: dict[str, Sample] = field(default_factory=dict)
    data_samples: dict[str, Sample] = field(default_factory=dict)
    progress_rows: int = 0
    # Parallel lists. Index 0 is the first column. Quoted means the cell
    # was wrapped in the quote mark, including "" and " ".
    column_names: list[str] = field(default_factory=list)
    column_quoted: list[int] = field(default_factory=list)
    column_bare: list[int] = field(default_factory=list)


def _is_number(text: str) -> bool:
    """True for a plain integer or decimal, including a leading sign."""
    if not text or len(text) > 64:
        return False
    body = text[1:] if text[0] in "+-" else text
    if not body:
        return False
    if "e" in body or "E" in body:
        return _NUM_RE.fullmatch(text) is not None
    if body.count(".") > 1:
        return False
    digits = body.replace(".", "", 1)
    return bool(digits) and digits.isdigit()


def _find_any(data: str, start: int, end: int, chars: str) -> int:
    found = end
    for char in chars:
        j = data.find(char, start, end)
        if j != -1 and j < found:
            found = j
    if found < end:
        return found
    return -1


def _detect_encoding(prefix: bytes) -> str:
    if prefix.startswith(b"\xff\xfe") or prefix.startswith(b"\xfe\xff"):
        return "utf-16"
    if prefix.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    sample = prefix[:4096]
    if b"\x00" in sample:
        evens = sample[0::2].count(0)
        odds = sample[1::2].count(0)
        if odds > evens:
            return "utf-16-le"
        if evens > odds:
            return "utf-16-be"
    return "utf-8"


def _newline_label(counts: Counter[str]) -> str:
    order = ("\r\n", "\n", "\r")
    names = {"\r\n": "CRLF", "\n": "LF", "\r": "CR"}
    present = [key for key in order if counts.get(key)]
    if not present:
        return "none"
    if len(present) == 1:
        return names[present[0]]
    return "mixed (" + ", ".join(names[key] for key in present) + ")"


class Scanner:
    """One streaming pass over CSV text."""

    def __init__(
        self,
        dialect: Dialect,
        *,
        header: bool,
        max_rows: int,
        collect_samples: bool,
        on_progress=None,
    ) -> None:
        if len(dialect.delim) != 1 or len(dialect.quote) != 1:
            raise ValueError("delimiter and quote must be a single character")
        if dialect.delim == dialect.quote:
            raise ValueError("delimiter and quote must be different characters")
        self.dialect = dialect
        self.delim = dialect.delim
        self.quote = dialect.quote
        self.expect_header = header
        self.max_rows = max_rows
        self.collect_samples = collect_samples
        self.on_progress = on_progress
        self.ignore_lone_blanks = False
        self.force_slow = False

        self.unquoted_chars = self.delim + self.quote + "\r\n"
        self.quoted_chars = self.quote + "\r\n"
        self.space_chars = "".join(ch for ch in " \t" if ch != self.delim)

        self.header = Bucket()
        self.data = Bucket()
        self.rec = Bucket()
        self.header_samples: dict[str, Sample] = {}
        self.data_samples: dict[str, Sample] = {}
        self.pending: list[tuple[str, Sample]] = []
        self.pending_kinds: set[str] = set()

        self.phase = "header" if header else "data"
        self.state = _START
        self.hold = ""
        self._stall = 0

        self.header_records = 0
        self.data_records = 0
        self.header_width = 0
        self.ragged = 0
        self.blank_lines = 0
        self.unterminated = 0
        self.first_unterminated: int | None = None
        self.junk_after_quote = 0
        self.padded_quotes = 0
        self.hit_max = False
        self.record_open = False
        self.row_no = 0
        self.newlines: Counter[str] = Counter()
        self.widths: Counter[int] = Counter()
        self._progress_mark = 500_000
        self.buf: list[str] = []
        self.num_parts: list[str] = []
        self._need_quote_sample = True
        self.header_names: list[str] = []
        self.col_quoted: list[int] = []
        self.col_bare: list[int] = []
        self._field_flags: list[bool] = []
        self._name_parts: list[str] = []
        self._name_buf: list[str] = []
        self._name_len = 0

        self._reset_field()

    def _note_progress(self) -> None:
        if self.data_records >= 40:
            self.collect_samples = False
        if self.on_progress and self.data_records >= self._progress_mark:
            self.on_progress(self.data_records)
            self._progress_mark += 500_000

    def _fast_block(self, data: str, *, final: bool) -> None:
        """Count quote-free text with split(). The slice must not contain a quote."""
        work = data
        if not final:
            if work.endswith("\r") and not work.endswith("\r\n"):
                self.hold = "\r"
                work = work[:-1]
            if work and not (work.endswith("\n") or work.endswith("\r")):
                cut = max(work.rfind("\n"), work.rfind("\r"))
                if cut == -1:
                    self.hold = work + self.hold
                    return True
                self.hold = work[cut + 1 :] + self.hold
                work = work[: cut + 1]
        if not work:
            return True
        crlf = work.count("\r\n")
        bare_lf = work.count("\n") - crlf
        bare_cr = work.count("\r") - crlf
        self.newlines["\r\n"] += crlf
        self.newlines["\n"] += bare_lf
        self.newlines["\r"] += bare_cr
        lines = work.splitlines()
        if not lines:
            # A chunk that is only a line break. splitlines() drops one break.
            lines = [""] * (crlf + bare_lf + bare_cr)
        for line in lines:
            if self.hit_max:
                break
            self._take_unquoted_line(line.split(self.delim))

    def _take_unquoted_line(self, parts: list[str]) -> None:
        self.row_no += 1
        lone_blank = len(parts) == 1 and parts[0] == ""
        skip_blank = self.ignore_lone_blanks or self.phase == "header" or (
            self.expect_header and self.header_width > 1
        )
        if lone_blank and skip_blank:
            self.blank_lines += 1
            return
        bucket = self.header if self.phase == "header" else self.data
        track = self.phase != "header"
        if track:
            self._ensure_columns(len(parts))
            bare_cols = self.col_bare
        else:
            self.header_names = parts
            bare_cols = None
        for index, part in enumerate(parts):
            bucket.fields += 1
            if part == "":
                bucket.empty_bare += 1
                kind = "bare_empty"
            elif (part[0] in " \t" or part[-1] in " \t") and not part.strip(
                self.space_chars
            ):
                bucket.ws_bare += 1
                kind = "ws_bare"
            else:
                bucket.bare += 1
                if part[0] in "0123456789+-." and _is_number(part):
                    bucket.numeric_bare += 1
                else:
                    bucket.text_bare += 1
                kind = "bare"
            if track:
                bare_cols[index] += 1
            if self.collect_samples:
                self._save_sample(kind, part)
        width = len(parts)
        if self.phase == "header":
            self.header_records += 1
            self.header_width = width
            self.phase = "data"
        else:
            self.data_records += 1
            if self.expect_header and self.header_width:
                if width != self.header_width:
                    self.ragged += 1
            else:
                self.widths[width] += 1
            if self.max_rows and self.data_records >= self.max_rows:
                self.hit_max = True
            self._note_progress()

    def _consume_balanced_block(self, data: str, i: int, end: int, final: bool) -> int:
        """Parse a run of complete lines whose quotes all close on that line.

        Returns how many characters were consumed. Zero means the caller
        should use another path, and nothing has been counted yet.
        """
        if i >= end:
            return 0
        limit = end
        if not final:
            chunk = data[i:end]
            if chunk.endswith("\r") and not chunk.endswith("\r\n"):
                return 0
            cut = max(chunk.rfind("\n"), chunk.rfind("\r"))
            if cut == -1:
                return 0
            limit = i + cut + 1
        if data.find(self.quote, i, limit) == -1:
            return 0
        block = data[i:limit]
        lines = block.splitlines()
        if not lines:
            return 0
        if any(line.count(self.quote) % 2 for line in lines):
            return 0
        pos = i
        for line in lines:
            if self.hit_max:
                break
            if line:
                if self.quote in line:
                    if not self._take_simple_quoted_line(line):
                        break
                else:
                    self._take_unquoted_line(line.split(self.delim))
            else:
                self._take_unquoted_line([""])
            pos += len(line)
            if pos < limit and data[pos] == "\r":
                if pos + 1 < limit and data[pos + 1] == "\n":
                    self.newlines["\r\n"] += 1
                    pos += 2
                else:
                    self.newlines["\r"] += 1
                    pos += 1
            elif pos < limit and data[pos] == "\n":
                self.newlines["\n"] += 1
                pos += 1
            if self.hit_max:
                break
        return pos - i

    def _try_simple_line(self, data: str, i: int, end: int, final: bool) -> int:
        """Parse one physical line that contains quotes but no line break inside them.

        Returns the index after the line, or -1 when the slow parser should take over.
        """
        nl = data.find("\n", i, end)
        cr = data.find("\r", i, end)
        if cr != -1 and (nl == -1 or cr < nl):
            if cr + 1 < end and data[cr + 1] == "\n":
                line_at, break_len, kind = cr, 2, "\r\n"
            elif cr + 1 == end and not final:
                return -1
            else:
                line_at, break_len, kind = cr, 1, "\r"
        elif nl != -1:
            line_at, break_len, kind = nl, 1, "\n"
        elif final:
            line_at, break_len, kind = end, 0, ""
        else:
            return -1
        line = data[i:line_at]
        if not line or line.count(self.quote) % 2:
            return -1
        if not self._take_simple_quoted_line(line):
            return -1
        if kind:
            self.newlines[kind] += 1
        return line_at + break_len

    def _take_simple_quoted_line(self, line: str) -> bool:
        """Tally one quote-balanced line. False leaves the scanner unchanged."""
        quote = self.quote
        delim = self.delim
        i = 0
        n = len(line)
        width = 0
        empty_q = empty_b = ws_q = ws_b = 0
        quoted_required = quoted_optional = bare = 0
        numeric_q = numeric_b = text_q = text_b = 0
        with_delim = with_inner = bare_quote = padded = 0
        samples: list[tuple[str, str]] = []
        flags: list[bool] = []
        grab_names = self.phase == "header"
        names: list[str] = []
        while i < n:
            char = line[i]
            if char == quote:
                k = i + 1
                saw_inner = False
                while k < n:
                    if line[k] == quote:
                        if k + 1 < n and line[k + 1] == quote:
                            saw_inner = True
                            k += 2
                            continue
                        break
                    k += 1
                else:
                    return False
                raw = line[i + 1 : k]
                value = raw.replace(quote + quote, quote) if saw_inner else raw
                needed = saw_inner or delim in raw
                width += 1
                if value == "":
                    empty_q += 1
                    kind = "quoted_empty"
                elif (value[0] in " \t" or value[-1] in " \t") and not value.strip(
                    self.space_chars
                ):
                    ws_q += 1
                    kind = "ws_quoted"
                elif needed:
                    quoted_required += 1
                    if value[0] in "0123456789+-." and _is_number(value):
                        numeric_q += 1
                    else:
                        text_q += 1
                    kind = "quoted_required"
                    if delim in raw:
                        with_delim += 1
                    if saw_inner:
                        with_inner += 1
                else:
                    quoted_optional += 1
                    if value[0] in "0123456789+-." and _is_number(value):
                        numeric_q += 1
                    else:
                        text_q += 1
                    kind = "quoted_optional"
                flags.append(True)
                if grab_names:
                    names.append(value)
                if self.collect_samples:
                    samples.append((kind, value))
                i = k + 1
                if i < n and line[i] in " \t":
                    padded += 1
                    while i < n and line[i] in " \t":
                        i += 1
                if i < n and line[i] == delim:
                    i += 1
                    continue
                if i == n:
                    break
                return False
            stop = line.find(delim, i)
            if stop == -1:
                part = line[i:]
                i = n
            else:
                part = line[i:stop]
                i = stop + 1
            if quote in part:
                return False
            width += 1
            if part == "":
                empty_b += 1
                kind = "bare_empty"
            elif (part[0] in " \t" or part[-1] in " \t") and not part.strip(
                self.space_chars
            ):
                ws_b += 1
                kind = "ws_bare"
            else:
                bare += 1
                if quote in part:
                    bare_quote += 1
                if part[0] in "0123456789+-." and _is_number(part):
                    numeric_b += 1
                else:
                    text_b += 1
                kind = "bare"
            flags.append(False)
            if grab_names:
                names.append(part)
            if self.collect_samples:
                samples.append((kind, part))
            if stop == -1:
                break
        if line.endswith(delim):
            empty_b += 1
            width += 1
            flags.append(False)
            if grab_names:
                names.append("")
            if self.collect_samples:
                samples.append(("bare_empty", ""))
        if width == 0:
            return False
        self._apply_line(
            width,
            empty_q=empty_q,
            empty_b=empty_b,
            ws_q=ws_q,
            ws_b=ws_b,
            quoted_required=quoted_required,
            quoted_optional=quoted_optional,
            bare=bare,
            numeric_q=numeric_q,
            numeric_b=numeric_b,
            text_q=text_q,
            text_b=text_b,
            with_delim=with_delim,
            with_inner=with_inner,
            bare_quote=bare_quote,
            samples=samples,
            flags=flags,
            names=names if grab_names else None,
        )
        self.padded_quotes += padded
        return True

    def _apply_line(
        self,
        width: int,
        *,
        empty_q: int,
        empty_b: int,
        ws_q: int,
        ws_b: int,
        quoted_required: int,
        quoted_optional: int,
        bare: int,
        numeric_q: int,
        numeric_b: int,
        text_q: int,
        text_b: int,
        with_delim: int,
        with_inner: int,
        bare_quote: int,
        samples: list[tuple[str, str]],
        flags: list[bool] | None = None,
        names: list[str] | None = None,
    ) -> None:
        self.row_no += 1
        lone_blank = width == 1 and empty_b == 1 and empty_q == 0
        skip_blank = self.ignore_lone_blanks or self.phase == "header" or (
            self.expect_header and self.header_width > 1
        )
        if lone_blank and skip_blank:
            self.blank_lines += 1
            return
        if self.phase == "header":
            if names is not None:
                self.header_names = names
        elif flags:
            self._note_column_flags(flags)
        bucket = self.header if self.phase == "header" else self.data
        bucket.fields += width
        bucket.empty_quoted += empty_q
        bucket.empty_bare += empty_b
        bucket.ws_quoted += ws_q
        bucket.ws_bare += ws_b
        bucket.quoted_required += quoted_required
        bucket.quoted_optional += quoted_optional
        bucket.bare += bare
        bucket.numeric_quoted += numeric_q
        bucket.numeric_bare += numeric_b
        bucket.text_quoted += text_q
        bucket.text_bare += text_b
        bucket.with_delim += with_delim
        bucket.with_inner_quote += with_inner
        bucket.bare_with_quote += bare_quote
        if self.collect_samples:
            for kind, text in samples:
                shown = text
                if kind == "quoted_empty":
                    shown = '""'
                elif kind in ("quoted_optional", "quoted_required", "ws_quoted"):
                    body = text.replace(self.quote, self.quote * 2)
                    if len(body) > 48:
                        body = body[:48] + "..."
                    shown = f"{self.quote}{body}{self.quote}"
                self._save_sample(kind, shown if kind != "bare" else text)
        if self.phase == "header":
            self.header_records += 1
            self.header_width = width
            self.phase = "data"
        else:
            self.data_records += 1
            if self.expect_header and self.header_width:
                if width != self.header_width:
                    self.ragged += 1
            else:
                self.widths[width] += 1
            if self.max_rows and self.data_records >= self.max_rows:
                self.hit_max = True
            self._note_progress()

    def _save_sample(self, kind: str, text: str) -> None:
        slot = self.header_samples if self.phase == "header" else self.data_samples
        if kind in slot:
            return
        if kind == "bare_empty":
            shown = "(blank)"
        elif kind == "ws_bare":
            shown = "[" + text[:48] + "]"
        elif len(text) > 48:
            shown = text[:48] + "..."
        else:
            shown = text
        slot[kind] = Sample(self.row_no, shown)

    def feed(self, text: str, *, final: bool, real_eof: bool) -> None:
        if self.hit_max:
            return
        data = self.hold + text
        self.hold = ""
        end = len(data)
        i = 0
        while i < end and not self.hit_max:
            if (
                not self.force_slow
                and self.state == _START
                and not self.record_open
            ):
                taken = self._consume_balanced_block(data, i, end, final)
                if taken > 0:
                    i += taken
                    if self.hit_max:
                        self.hold = ""
                        return
                    continue
            if (
                not self.force_slow
                and self.state == _START
                and not self.record_open
            ):
                quote_at = data.find(self.quote, i, end)
                if quote_at == -1:
                    self._fast_block(data[i:], final=final)
                    i = end
                    break
                # Keep every complete line before the quote on the fast path.
                # One quoted value must not drag the rest of the chunk with it.
                cut = max(data.rfind("\n", i, quote_at), data.rfind("\r", i, quote_at))
                if cut >= i:
                    self._fast_block(data[i : cut + 1], final=False)
                    i = cut + 1
                    continue
            if not self.force_slow and self.state == _START and not self.record_open:
                advanced = self._try_simple_line(data, i, end, final)
                if advanced > i:
                    i = advanced
                    if self.hit_max:
                        self.hold = ""
                        return
                    continue
            nxt = self._step(data, i, end, final)
            if nxt < 0:
                self.hold = data[self._stall :]
                return
            if nxt <= i:
                raise RuntimeError(
                    f"parser stalled at index {i} in state {self.state}"
                )
            i = nxt
            if self.hit_max:
                self.hold = ""
                return
        if final:
            self._close(real_eof=real_eof)

    def to_result(
        self,
        *,
        path: Path,
        size: int,
        encoding: str,
        encoding_note: str,
        elapsed: float,
    ) -> Scan:
        columns = self.header_width
        ragged = self.ragged
        if not self.expect_header and self.widths:
            mode = max(self.widths, key=lambda width: (self.widths[width], -width))
            columns = mode
            ragged = self.data_records - self.widths[mode]

        if self.data_records:
            judged = "data"
        elif self.header_records:
            judged = "header"
        else:
            judged = "none"

        quote_assumed = not self.header.has_quotes() and not self.data.has_quotes()
        low = False
        if self.data_records >= 10 and ragged / self.data_records >= 0.05:
            low = True

        return Scan(
            path=path,
            size=size,
            elapsed=elapsed,
            encoding=encoding,
            encoding_note=encoding_note,
            delim=self.delim,
            delim_status=self.dialect.delim_status,
            quote=self.quote,
            quote_assumed=quote_assumed,
            newline_label=_newline_label(self.newlines),
            columns=columns,
            header=self.header,
            data=self.data,
            header_records=self.header_records,
            data_records=self.data_records,
            ragged=ragged,
            blank_lines=self.blank_lines,
            unterminated=self.unterminated,
            first_unterminated=self.first_unterminated,
            junk_after_quote=self.junk_after_quote,
            padded_quotes=self.padded_quotes,
            low_confidence=low,
            hit_max=self.hit_max,
            max_rows=self.max_rows,
            expect_header=self.expect_header,
            judged=judged,
            header_samples=self.header_samples,
            data_samples=self.data_samples,
            column_names=list(self.header_names),
            column_quoted=list(self.col_quoted),
            column_bare=list(self.col_bare),
        )

    def _ensure_columns(self, width: int) -> None:
        have = len(self.col_quoted)
        if width <= have:
            return
        extra = width - have
        self.col_quoted.extend([0] * extra)
        self.col_bare.extend([0] * extra)

    def _note_column_flags(self, flags: list[bool]) -> None:
        self._ensure_columns(len(flags))
        quoted = self.col_quoted
        bare = self.col_bare
        for index, is_quoted in enumerate(flags):
            if is_quoted:
                quoted[index] += 1
            else:
                bare[index] += 1

    def _reset_field(self) -> None:
        self.field_quoted = False
        self.saw_delim = False
        self.saw_newline = False
        self.saw_quote = False
        self.saw_pad = False
        self.field_bad = False
        self.has_chars = False
        self.ws_only = True
        self.num_possible = True
        self.num_len = 0
        self.buf_len = 0
        self.truncated = False
        self._fast = False
        self.buf.clear()
        self.num_parts.clear()
        self._name_buf.clear()
        self._name_len = 0

    def _reset_rec(self) -> None:
        self.rec.reset()
        self.pending.clear()
        self.pending_kinds.clear()
        self._field_flags.clear()
        self._name_parts.clear()
        self.record_open = False
        self.state = _START
        self._reset_field()

    def _touch(self) -> None:
        if not self.record_open:
            self.record_open = True
            self.row_no += 1

    def _add(self, text: str) -> None:
        if not text:
            return
        self.has_chars = True
        if self.phase == "header" and self._name_len < 200:
            room = 200 - self._name_len
            piece = text if len(text) <= room else text[:room]
            self._name_buf.append(piece)
            self._name_len += len(piece)
        if not self.saw_delim and self.delim in text:
            self.saw_delim = True
        if self._fast:
            return
        capture = self.collect_samples or (
            self.field_quoted and self._need_quote_sample
        )
        if capture:
            if self.buf_len < _SAMPLE_CHARS:
                room = _SAMPLE_CHARS - self.buf_len
                if len(text) <= room:
                    self.buf.append(text)
                    self.buf_len += len(text)
                else:
                    self.buf.append(text[:room])
                    self.buf_len += room
                    self.truncated = True
            else:
                self.truncated = True
        if self.ws_only and text.strip(self.space_chars):
            self.ws_only = False
        if self.num_possible:
            # strip() here asks whether any character sits outside the
            # numeric set. One C call, not a Python loop per character.
            if self.num_len + len(text) > 64 or text.strip(_NUM_STR):
                self.num_possible = False
                self.num_parts.clear()
                self.num_len = 0
            else:
                self.num_parts.append(text)
                self.num_len += len(text)
        if (
            self.saw_delim
            and (not self.collect_samples or self.buf_len >= _SAMPLE_CHARS)
            and not self.ws_only
            and not self.num_possible
        ):
            self._fast = True

    def _classify(self) -> tuple[str, bool]:
        if not self.has_chars:
            kind = "quoted_empty" if self.field_quoted else "bare_empty"
            return kind, False
        if self.ws_only:
            kind = "ws_quoted" if self.field_quoted else "ws_bare"
            return kind, False
        is_num = False
        if self.num_possible and self.num_parts:
            is_num = _is_number("".join(self.num_parts))
        if self.field_quoted:
            needed = self.saw_delim or self.saw_newline or self.saw_quote
            kind = "quoted_required" if needed else "quoted_optional"
        else:
            kind = "bare"
        return kind, is_num

    def _tally(self, kind: str, is_num: bool) -> None:
        rec = self.rec
        if kind == "quoted_empty":
            rec.empty_quoted += 1
        elif kind == "bare_empty":
            rec.empty_bare += 1
        elif kind == "ws_quoted":
            rec.ws_quoted += 1
        elif kind == "ws_bare":
            rec.ws_bare += 1
        elif kind == "quoted_required":
            rec.quoted_required += 1
            if is_num:
                rec.numeric_quoted += 1
            else:
                rec.text_quoted += 1
        elif kind == "quoted_optional":
            rec.quoted_optional += 1
            if is_num:
                rec.numeric_quoted += 1
            else:
                rec.text_quoted += 1
        else:
            rec.bare += 1
            if is_num:
                rec.numeric_bare += 1
            else:
                rec.text_bare += 1
            if self.saw_quote:
                rec.bare_with_quote += 1
        if self.saw_delim:
            rec.with_delim += 1
        if self.saw_newline:
            rec.with_break += 1
        if self.saw_quote and self.field_quoted:
            rec.with_inner_quote += 1

    def _preview(self, kind: str) -> str:
        if kind == "quoted_empty":
            return '""'
        if kind == "bare_empty":
            return "(blank)"
        raw = "".join(self.buf)
        raw = raw.replace("\r", "\\n").replace("\n", "\\n").replace("\t", "\\t")
        if kind in ("quoted_optional", "quoted_required", "ws_quoted"):
            body = raw.replace('"', '""')
            suffix = "..." if self.truncated else ""
            return f'"{body}{suffix}"'
        if kind == "ws_bare":
            suffix = "..." if self.truncated else ""
            return f"[{raw}{suffix}]"
        suffix = "..." if self.truncated else ""
        return raw + suffix

    def _remember(self, kind: str) -> None:
        quoted_kind = kind in (
            "quoted_optional",
            "quoted_required",
            "quoted_empty",
            "ws_quoted",
        )
        slot = self.header_samples if self.phase == "header" else self.data_samples
        if kind in slot or kind in self.pending_kinds:
            if quoted_kind:
                self._need_quote_sample = False
            return
        if quoted_kind:
            self._need_quote_sample = False
        useful = self.collect_samples or quoted_kind
        if not useful:
            return
        self.pending_kinds.add(kind)
        self.pending.append((kind, Sample(self.row_no, self._preview(kind))))

    def _commit_field(self) -> None:
        if self.saw_pad:
            self.padded_quotes += 1
        kind, is_num = self._classify()
        self._tally(kind, is_num)
        self.rec.fields += 1
        if self.phase == "header":
            self._name_parts.append("".join(self._name_buf))
        else:
            self._field_flags.append(self.field_quoted)
        self._remember(kind)
        self._reset_field()
        self.state = _START

    def _commit_empty(self, quoted: bool) -> None:
        self.field_quoted = quoted
        self.has_chars = False
        self._commit_field()

    def _is_ignorable_blank(self) -> bool:
        if not (self.rec.fields == 1 and self.rec.empty_bare == 1):
            return False
        if self.ignore_lone_blanks or self.phase == "header":
            return True
        return self.expect_header and self.header_width > 1

    def _flush_record(self) -> None:
        if self._is_ignorable_blank():
            self.blank_lines += 1
            self._reset_rec()
            return
        slot = self.header_samples if self.phase == "header" else self.data_samples
        for kind, sample in self.pending:
            if kind not in slot:
                slot[kind] = sample
        if self.phase == "header":
            if self._name_parts:
                self.header_names = list(self._name_parts)
            self.header.absorb(self.rec)
            self.header_records += 1
            self.header_width = self.rec.fields
            self.phase = "data"
        else:
            if self._field_flags:
                self._note_column_flags(self._field_flags)
            width = self.rec.fields
            self.data.absorb(self.rec)
            self.data_records += 1
            if self.expect_header and self.header_width:
                if width != self.header_width:
                    self.ragged += 1
            else:
                self.widths[width] += 1
            if self.max_rows and self.data_records >= self.max_rows:
                self.hit_max = True
            self._note_progress()
        self._reset_rec()

    def _nl_ready(self, data: str, j: int, end: int, final: bool) -> bool:
        if data[j] == "\r" and j + 1 >= end and not final:
            self._stall = j
            return False
        return True

    def _skip_nl(self, data: str, j: int, end: int) -> int:
        if data[j] == "\r" and j + 1 < end and data[j + 1] == "\n":
            self.newlines["\r\n"] += 1
            return j + 2
        if data[j] == "\r":
            self.newlines["\r"] += 1
            return j + 1
        self.newlines["\n"] += 1
        return j + 1

    def _close(self, *, real_eof: bool) -> None:
        if self.hit_max:
            return
        if not real_eof:
            self._reset_rec()
            self.hold = ""
            return
        if self.state == _QUOTED:
            self.unterminated += 1
            if self.first_unterminated is None:
                self.first_unterminated = self.row_no or 1
            self._commit_field()
            self._flush_record()
        elif self.state in (_UNQUOTED, _CLOSED):
            self._commit_field()
            self._flush_record()
        elif self.state == _START and self.record_open and self.rec.fields > 0:
            self._commit_empty(False)
            self._flush_record()
        self.hold = ""

    def _step(self, data: str, i: int, end: int, final: bool) -> int:
        if self.state == _START:
            self._touch()
            char = data[i]
            if char == self.quote:
                self.field_quoted = True
                self.state = _QUOTED
                return i + 1
            if char == self.delim:
                self._commit_empty(False)
                return i + 1
            if char in "\r\n":
                if not self._nl_ready(data, i, end, final):
                    return -1
                self._commit_empty(False)
                self._flush_record()
                return self._skip_nl(data, i, end)
            self.state = _UNQUOTED

        if self.state == _CLOSED:
            char = data[i]
            if char == self.delim:
                self._commit_field()
                return i + 1
            if char in "\r\n":
                if not self._nl_ready(data, i, end, final):
                    return -1
                self._commit_field()
                self._flush_record()
                return self._skip_nl(data, i, end)
            if char in self.space_chars:
                self.saw_pad = True
                return i + 1
            if not self.field_bad:
                self.field_bad = True
                self.junk_after_quote += 1
            self.state = _UNQUOTED

        if self.state == _UNQUOTED:
            return self._scan_unquoted(data, i, end, final)
        if self.state == _QUOTED:
            return self._scan_quoted(data, i, end, final)
        raise RuntimeError(f"unknown state {self.state}")

    def _scan_unquoted(self, data: str, i: int, end: int, final: bool) -> int:
        j = _find_any(data, i, end, self.unquoted_chars)
        if j == -1:
            self._add(data[i:end])
            if end <= i:
                raise RuntimeError("parser stalled in an unquoted field")
            return end
        self._add(data[i:j])
        char = data[j]
        if char == self.delim:
            self._commit_field()
            return j + 1
        if char in "\r\n":
            if not self._nl_ready(data, j, end, final):
                return -1
            self._commit_field()
            self._flush_record()
            return self._skip_nl(data, j, end)
        self.saw_quote = True
        self._add(char)
        return j + 1

    def _scan_quoted(self, data: str, i: int, end: int, final: bool) -> int:
        j = _find_any(data, i, end, self.quoted_chars)
        if j == -1:
            self._add(data[i:end])
            if end <= i:
                raise RuntimeError("parser stalled in a quoted field")
            return end
        self._add(data[i:j])
        char = data[j]
        if char in "\r\n":
            if not self._nl_ready(data, j, end, final):
                return -1
            self.saw_newline = True
            self._add("\n")
            return self._skip_nl(data, j, end)
        if j + 1 >= end and not final:
            self._stall = j
            return -1
        if j + 1 < end and data[j + 1] == self.quote:
            self.saw_quote = True
            self._add(self.quote)
            return j + 2
        self.state = _CLOSED
        return j + 1


def _sniff_score(text: str, delim: str, quote: str, file_has_more: bool) -> tuple[float, float, int, int]:
    dialect = Dialect(delim, quote, "detected", "detected")
    scanner = Scanner(
        dialect,
        header=False,
        max_rows=_SNIFF_ROWS,
        collect_samples=False,
    )
    # A blank line is one empty field. Counting it makes a real comma file
    # look less consistent than "this is a single column".
    scanner.ignore_lone_blanks = True
    scanner.feed(text, final=True, real_eof=not file_has_more)
    records = scanner.data_records
    if records == 0:
        return (-1.0, 0.0, 0, 0)
    mode = max(scanner.widths, key=lambda width: (scanner.widths[width], -width))
    mode_n = scanner.widths[mode]
    consistent = mode_n / records
    quoted = (
        scanner.data.empty_quoted
        + scanner.data.quoted_optional
        + scanner.data.quoted_required
        + scanner.data.ws_quoted
    )
    score = consistent * 1000 + mode * 25
    # A one-column reading matches every delimiter. Prefer a reading that
    # actually separates columns when one is available.
    if mode <= 1:
        score -= 800
    if quoted:
        score += 100
    score -= min(scanner.unterminated, 3) * 70
    score -= min(scanner.data.bare_with_quote, 10) * 25
    score -= scanner.junk_after_quote * 40
    return (score, consistent, mode, quoted)


def choose_dialect(
    text: str,
    *,
    file_has_more: bool,
    delim: str | None,
    quote: str | None,
) -> Dialect:
    if delim and quote:
        return Dialect(delim, quote, "given", "given")

    delims = [delim] if delim else [",", "|", "\t", ";"]
    quotes = [quote] if quote else ['"', "'"]
    ranked: list[tuple[tuple[float, float, int, int], int, int, str, str]] = []
    for d_i, candidate_delim in enumerate(delims):
        for q_i, candidate_quote in enumerate(quotes):
            if candidate_delim == candidate_quote:
                continue
            score = _sniff_score(text, candidate_delim, candidate_quote, file_has_more)
            ranked.append((score, d_i, q_i, candidate_delim, candidate_quote))
    if not ranked:
        raise ValueError("delimiter and quote must be different characters")

    score, _d_i, _q_i, best_delim, best_quote = max(
        ranked, key=lambda item: (item[0][0], -item[1], -item[2])
    )
    modes = [item[0][2] for item in ranked]
    if delim:
        delim_status = "given"
    elif max(modes) <= 1:
        delim_status = "guessed"
    else:
        delim_status = "detected"
    quote_status = "given" if quote else "detected"
    # score is unused beyond selection; quoted count lives in score[3]
    _ = score
    return Dialect(best_delim, best_quote, delim_status, quote_status)


def scan_text(
    text: str,
    *,
    delimiter: str | None = None,
    quote: str | None = None,
    header: bool = True,
    max_rows: int = 0,
    chunk_size: int | None = None,
    force_slow: bool = False,
) -> Scan:
    """Scan CSV text that is already in memory. Used to check the parser."""
    dialect = choose_dialect(
        text,
        file_has_more=False,
        delim=delimiter,
        quote=quote,
    )
    scanner = Scanner(
        dialect,
        header=header,
        max_rows=max_rows,
        collect_samples=True,
    )
    scanner.force_slow = force_slow
    if text and chunk_size and chunk_size > 0:
        for start in range(0, len(text), chunk_size):
            piece = text[start : start + chunk_size]
            last = start + chunk_size >= len(text)
            scanner.feed(piece, final=last, real_eof=last)
            if scanner.hit_max:
                break
    else:
        scanner.feed(text, final=True, real_eof=True)
    return scanner.to_result(
        path=Path("<memory>"),
        size=len(text.encode("utf-8")),
        encoding="utf-8",
        encoding_note="",
        elapsed=0.0,
    )


def scan_path(
    path: Path,
    *,
    delimiter: str | None = None,
    quote: str | None = None,
    encoding: str | None = None,
    header: bool = True,
    max_rows: int = 0,
    on_progress=None,
    encoding_note: str = "",
    allow_fallback: bool = True,
) -> Scan:
    """Scan one CSV file in a single forward read after a short dialect sniff."""
    path = Path(path)
    try:
        if not path.is_file():
            return Scan(path=path, error="File not found.")
        size = path.stat().st_size
        with path.open("rb") as handle:
            probe = handle.read(4096)
        chosen = encoding or _detect_encoding(probe)
        try:
            return _scan_body(
                path,
                size=size,
                encoding=chosen,
                encoding_note=encoding_note,
                delimiter=delimiter,
                quote=quote,
                header=header,
                max_rows=max_rows,
                on_progress=on_progress,
            )
        except UnicodeDecodeError:
            if allow_fallback and encoding is None and chosen in ("utf-8", "utf-8-sig"):
                return scan_path(
                    path,
                    delimiter=delimiter,
                    quote=quote,
                    encoding="cp1252",
                    header=header,
                    max_rows=max_rows,
                    on_progress=on_progress,
                    encoding_note="Not valid UTF-8. Read as Windows-1252.",
                    allow_fallback=False,
                )
            return Scan(path=path, error=f"Could not decode this file as {chosen}.")
    except OSError as exc:
        reason = exc.strerror or str(exc)
        return Scan(path=path, error=f"Could not read this file. {reason}")


def _scan_body(
    path: Path,
    *,
    size: int,
    encoding: str,
    encoding_note: str,
    delimiter: str | None,
    quote: str | None,
    header: bool,
    max_rows: int,
    on_progress,
) -> Scan:
    started = time.perf_counter()
    with path.open("rb") as handle:
        prefix_raw = handle.read(min(size, _PREFIX_BYTES))
    file_has_more = size > len(prefix_raw)
    prefix = prefix_raw.decode(encoding)
    dialect = choose_dialect(
        prefix,
        file_has_more=file_has_more,
        delim=delimiter,
        quote=quote,
    )
    scanner = Scanner(
        dialect,
        header=header,
        max_rows=max_rows,
        collect_samples=True,
        on_progress=on_progress,
    )
    decoder = codecs.getincrementaldecoder(encoding)()
    with path.open("rb", buffering=_CHUNK_BYTES) as handle:
        while True:
            raw = handle.read(_CHUNK_BYTES)
            if not raw:
                tail = decoder.decode(b"", final=True)
                scanner.feed(tail, final=True, real_eof=True)
                break
            scanner.feed(decoder.decode(raw), final=False, real_eof=False)
            if scanner.hit_max:
                break
        return scanner.to_result(
            path=path,
            size=size,
            encoding=encoding,
            encoding_note=encoding_note,
            elapsed=time.perf_counter() - started,
        )
