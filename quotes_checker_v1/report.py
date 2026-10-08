"""Sectioned terminal report for the quotes checker."""

from __future__ import annotations

import textwrap
from pathlib import Path

from .scan import Bucket, Sample, Scan


_WIDTH = 72
_RULE = "=" * _WIDTH
_SUB = "-" * _WIDTH

_SAMPLE_ORDER = (
    ("quoted_optional", "Quoted by choice"),
    ("quoted_required", "Quoted as needed"),
    ("bare", "Bare value"),
    ("quoted_empty", "Quoted empty"),
    ("bare_empty", "Bare empty"),
    ("ws_quoted", "Quoted spaces"),
    ("ws_bare", "Bare spaces"),
)


class Habit:
    def __init__(
        self,
        value_label: str,
        empty_label: str,
        value_sentence: str,
        empty_sentence: str,
        caveat: str,
    ) -> None:
        self.value_label = value_label
        self.empty_label = empty_label
        self.value_sentence = value_sentence
        self.empty_sentence = empty_sentence
        self.caveat = caveat

    @property
    def summary(self) -> str:
        if self.value_label == "no values" and self.empty_label == "none seen":
            return "Nothing to judge."
        if self.value_label == "mixed" or self.empty_label == "mixed":
            return "Mixed quoting."
        return "Uniform quoting."


def describe(bucket: Bucket) -> Habit:
    quoted = bucket.quoted_optional + bucket.quoted_required
    bare = bucket.bare
    value_label, value_sentence = _value_habit(bucket, quoted, bare)
    empty_label, empty_sentence = _empty_habit(bucket)
    caveat = _caveat(bucket, value_label)
    return Habit(value_label, empty_label, value_sentence, empty_sentence, caveat)


def column_labels(scan: Scan) -> list[str]:
    width = max(
        len(scan.column_names),
        len(scan.column_quoted),
        len(scan.column_bare),
        scan.columns,
    )
    raw: list[str] = []
    for index in range(width):
        name = ""
        if index < len(scan.column_names):
            name = (
                scan.column_names[index]
                .replace("\r", " ")
                .replace("\n", " ")
                .strip()
            )
        raw.append(name or f"column {index + 1}")
    counts: dict[str, int] = {}
    for name in raw:
        counts[name] = counts.get(name, 0) + 1
    labels: list[str] = []
    for index, name in enumerate(raw):
        if counts[name] > 1:
            labels.append(f"{name} (column {index + 1})")
        else:
            labels.append(name)
    return labels


def column_groups(scan: Scan) -> tuple[list[str], list[str], list[str]]:
    """Quoted columns, unquoted columns, and columns that use both."""
    labels = column_labels(scan)
    with_quotes: list[str] = []
    without: list[str] = []
    mixed: list[str] = []
    for index, label in enumerate(labels):
        quoted = scan.column_quoted[index] if index < len(scan.column_quoted) else 0
        bare = scan.column_bare[index] if index < len(scan.column_bare) else 0
        if quoted and bare:
            mixed.append(label)
        elif quoted:
            with_quotes.append(label)
        elif bare:
            without.append(label)
    return with_quotes, without, mixed


def render_terminal(scans: list[Scan]) -> str:
    """The short list printed in the terminal: quoted columns, then bare columns."""
    blocks: list[str] = []
    for scan in scans:
        lines = [scan.path.name]
        if scan.error:
            lines.append(f"  {scan.error}")
            blocks.append("\n".join(lines))
            continue
        with_quotes, without, mixed = column_groups(scan)
        lines.append('Columns with "":')
        if with_quotes:
            lines.extend(f"  {name}" for name in with_quotes)
        else:
            lines.append("  (none)")
        lines.append('Columns without "":')
        if without:
            lines.extend(f"  {name}" for name in without)
        else:
            lines.append("  (none)")
        if mixed:
            lines.append("Columns with both:")
            lines.extend(f"  {name}" for name in mixed)
        blocks.append("\n".join(lines))
    if not blocks:
        return ""
    return "\n\n".join(blocks) + "\n"


def write_reports(scans: list[Scan], folder: Path, *, max_rows: int) -> list[Path]:
    """Write one detail file per CSV. The file name follows the CSV name."""
    folder.mkdir(parents=True, exist_ok=True)
    used: set[str] = set()
    written: list[Path] = []
    for scan in scans:
        stem = scan.path.name or "report"
        candidate = f"{stem}.txt"
        number = 2
        while candidate.lower() in used:
            candidate = f"{stem}-{number}.txt"
            number += 1
        used.add(candidate.lower())
        dest = folder / candidate
        dest.write_text(render([scan], max_rows=max_rows), encoding="utf-8")
        written.append(dest)
    return written


def render(scans: list[Scan], *, max_rows: int) -> str:
    lines: list[str] = [_RULE, "  QUOTES CHECK", _RULE, ""]
    lines.extend(_how(max_rows))
    total = len(scans)
    for index, scan in enumerate(scans, start=1):
        lines.append("")
        lines.extend(_file_block(scan, index, total))
    usable = [scan for scan in scans if scan.error is None]
    if len(usable) >= 2:
        lines.append("")
        lines.extend(_across(usable))
    lines.append("")
    return "\n".join(lines) + "\n"


def _how(max_rows: int) -> list[str]:
    paragraphs = [
        (
            "Every row is read once. The checker keeps counts and a few examples, "
            "then drops the row, so a large file stays out of memory."
        ),
        (
            "The count covers the whole file because quoting often shows up late. "
            "Some writers add quotes only when a value contains the delimiter, a "
            "quote, or a line break. Some write empty values as \"\" on only part "
            "of the rows. The opening rows can hide both habits."
        ),
        (
            "A quoted empty value is \"\". A quote stored inside a value is doubled. "
            "Row numbers start at 1 and include the header. A line break inside "
            "quotes stays in the same row."
        ),
    ]
    if max_rows > 0:
        paragraphs.append(
            f"This run reads at most {max_rows:,} data rows from each file. "
            "Rows after that can still carry a different habit, so the verdict "
            "covers only the rows that were read."
        )
    lines = [_SUB, "  HOW THIS CHECK WORKS", _SUB]
    for paragraph in paragraphs:
        lines.append(_wrap(paragraph))
        lines.append("")
    if lines[-1] == "":
        lines.pop()
    return lines


def _file_block(scan: Scan, index: int, total: int) -> list[str]:
    title = _file_title(scan.path, index, total)
    lines = [_SUB, title, _SUB]
    if scan.error:
        lines.append(_kv("Path", str(scan.path)))
        lines.append(_wrap(scan.error))
        return lines

    lines.append(_kv("Path", str(scan.path)))
    lines.append(_kv("Size", _fmt_size(scan.size)))
    lines.append(_kv("Read in", _fmt_seconds(scan.elapsed)))
    encoding = scan.encoding or "unknown"
    if scan.encoding_note:
        encoding = f"{encoding}  ({scan.encoding_note})"
    lines.append(_kv("Encoding", encoding))
    lines.append(_kv("Delimiter", _fmt_delim(scan)))
    lines.append(_kv("Quote mark", _fmt_quote(scan)))
    lines.append(_kv("Line ending", scan.newline_label))
    lines.append(_kv("Columns", f"{scan.columns:,}" if scan.columns else "0"))
    lines.extend(_column_block(scan))

    if scan.header_records == 0 and scan.data_records == 0:
        lines.append("")
        if scan.blank_lines:
            lines.append(
                _wrap(
                    f"The file has {scan.blank_lines:,} blank lines and no other rows. "
                    "A blank line is a single empty field, so a one-column file of "
                    "empty values looks the same."
                )
            )
        else:
            lines.append(_wrap("The file has no rows."))
        return lines

    if scan.expect_header:
        lines.append("")
        lines.append("  HEADER ROW")
        if scan.header_records:
            lines.append(_count("Rows", scan.header_records))
            lines.extend(_counts(scan.header))
        else:
            lines.append("    No header row.")

    lines.append("")
    lines.append("  DATA ROWS")
    lines.append(_count("Rows", scan.data_records))
    lines.extend(_counts(scan.data))

    habit_bucket = scan.data if scan.judged == "data" else scan.header
    habit = describe(habit_bucket) if scan.judged != "none" else None
    lines.append("")
    lines.append("  VERDICT")
    if habit is None:
        lines.append(_wrap("Nothing to judge."))
    else:
        source = "data rows" if scan.judged == "data" else "the header row"
        lines.append(_kv("Judged from", source))
        lines.append(_kv("Values", habit.value_label))
        lines.append(_kv("Empties", habit.empty_label))
        lines.append(_kv("Habit", habit.summary))
        lines.append("")
        lines.append(_wrap(habit.value_sentence))
        lines.append(_wrap(habit.empty_sentence))
        if habit.caveat:
            lines.append(_wrap(habit.caveat))
        if scan.judged == "header" and scan.expect_header:
            lines.append(_wrap("The file has no data rows. This verdict is the header."))
        if scan.hit_max:
            lines.append(
                _wrap(
                    f"Stopped after {scan.data_records:,} data rows because --max-rows was set."
                )
            )
        if (
            scan.expect_header
            and scan.header_records
            and scan.data_records
            and _habits_differ(scan.header, scan.data)
        ):
            lines.append(
                _wrap("The header follows a different habit from the data rows.")
            )

    samples = scan.data_samples if scan.judged == "data" else scan.header_samples
    sample_lines = _samples(samples)
    if sample_lines:
        lines.append("")
        lines.append("  SAMPLES")
        lines.extend(sample_lines)

    check_lines = _checks(scan)
    if check_lines:
        lines.append("")
        lines.append("  CHECKS")
        lines.extend(check_lines)
    return lines


def _column_block(scan: Scan) -> list[str]:
    labels = column_labels(scan)
    lines = ["", "  COLUMNS"]
    if not labels:
        lines.append("    No columns were read.")
        return lines
    with_quotes, without, mixed = column_groups(scan)
    lines.append('    With "":')
    if with_quotes:
        lines.extend(f"      {name}" for name in with_quotes)
    else:
        lines.append("      (none)")
    lines.append('    Without "":')
    if without:
        lines.extend(f"      {name}" for name in without)
    else:
        lines.append("      (none)")
    if mixed:
        lines.append("    Both:")
        lines.extend(f"      {name}" for name in mixed)
    lines.append("")
    rows: list[list[str]] = []
    for index, label in enumerate(labels):
        quoted = scan.column_quoted[index] if index < len(scan.column_quoted) else 0
        bare = scan.column_bare[index] if index < len(scan.column_bare) else 0
        shown = label if len(label) <= 36 else label[:33] + "..."
        rows.append([shown, f"{quoted:,}", f"{bare:,}"])
    lines.append(_table(["Column", "Quoted cells", "Bare cells"], rows))
    return lines


def _counts(bucket: Bucket) -> list[str]:
    quoted = bucket.quoted_optional + bucket.quoted_required
    lines = [
        _count("Fields", bucket.fields),
        _count("Quoted values", quoted),
        _count("needed quotes", bucket.quoted_required, sub=True),
        _count("quoted by choice", bucket.quoted_optional, sub=True),
    ]
    if bucket.with_delim:
        lines.append(_count("with a delimiter inside", bucket.with_delim, sub=True))
    if bucket.with_inner_quote:
        lines.append(_count("with a quote inside", bucket.with_inner_quote, sub=True))
    if bucket.with_break:
        lines.append(_count("with a line break", bucket.with_break, sub=True))
    lines.append(_count("Bare values", bucket.bare))
    lines.append(_count('Quoted empties ("")', bucket.empty_quoted))
    lines.append(_count("Bare empties", bucket.empty_bare))
    if bucket.ws_quoted or bucket.ws_bare:
        lines.append(_count("Whitespace-only quoted", bucket.ws_quoted))
        lines.append(_count("Whitespace-only bare", bucket.ws_bare))
    return lines


def _checks(scan: Scan) -> list[str]:
    lines: list[str] = []
    judged = scan.data if scan.judged == "data" else scan.header
    if scan.ragged:
        lines.append(
            _wrap(
                f"Rows with a different column count: {scan.ragged:,}."
            )
        )
    if scan.low_confidence:
        lines.append(
            _wrap(
                "Enough rows disagree on the column count that the delimiter "
                "or the quote mark may be wrong. Pass --delimiter or --quote "
                "and run the file again if this looks off."
            )
        )
    if scan.unterminated:
        where = ""
        if scan.first_unterminated:
            where = f" The first one starts at row {scan.first_unterminated:,}."
        lines.append(
            _wrap(f"Unterminated quotes: {scan.unterminated:,}.{where}")
        )
    if scan.junk_after_quote:
        lines.append(
            _wrap(
                f"Fields with text after the closing quote: {scan.junk_after_quote:,}."
            )
        )
    if judged.bare_with_quote:
        lines.append(
            _wrap(
                f"Bare values containing a quote mark: {judged.bare_with_quote:,}. "
                "The mark is part of the value, or the quote character is wrong."
            )
        )
    if scan.padded_quotes:
        lines.append(
            _wrap(
                f"Closing quotes with spaces before the delimiter: {scan.padded_quotes:,}."
            )
        )
    if scan.blank_lines:
        lines.append(
            _wrap(
                f"Blank lines skipped: {scan.blank_lines:,}. "
                "They are single empty fields and are left out of the empty-value counts "
                "when the file has more than one column."
            )
        )
    if scan.delim_status == "guessed":
        lines.append(
            _wrap(
                "The delimiter is a guess. Nothing in the opening rows separates "
                "one column into several, so comma, pipe, tab, and semicolon "
                "all fit. Pass --delimiter if you know it."
            )
        )
    return lines


def _samples(samples: dict[str, Sample]) -> list[str]:
    lines: list[str] = []
    for key, label in _SAMPLE_ORDER:
        sample = samples.get(key)
        if sample is None:
            continue
        lines.append(f"    {label:<22} row {sample.row:<7} {sample.text}")
    return lines


def _across(scans: list[Scan]) -> list[str]:
    lines = [_RULE, "  ACROSS FILES", _RULE]
    show_source = any(scan.judged != "data" for scan in scans)
    headers = ["File", "Values", "Empties"]
    if show_source:
        headers.append("Judged from")
    rows: list[list[str]] = []
    value_labels: list[str] = []
    empty_labels: list[str] = []
    names = _display_names([scan.path for scan in scans])
    for scan, name in zip(scans, names):
        bucket = scan.data if scan.judged == "data" else scan.header
        if scan.judged == "none":
            value_label, empty_label = "no values", "none seen"
        else:
            habit = describe(bucket)
            value_label, empty_label = habit.value_label, habit.empty_label
        value_labels.append(value_label)
        empty_labels.append(empty_label)
        row = [name, value_label, empty_label]
        if show_source:
            row.append("header" if scan.judged == "header" else scan.judged)
        rows.append(row)
    lines.append(_table(headers, rows))
    lines.append("")
    lines.append(_wrap(_across_sentence(value_labels, empty_labels)))
    return lines


def _across_sentence(value_labels: list[str], empty_labels: list[str]) -> str:
    values_same = len(set(value_labels)) == 1
    empties_same = len(set(empty_labels)) == 1
    if values_same and empties_same:
        return (
            "Value quoting matches across these files, and empty quoting matches too."
        )
    parts: list[str] = []
    if values_same:
        parts.append("Value quoting matches across these files.")
    else:
        parts.append("Value quoting differs across these files.")
    if empties_same:
        parts.append("Empty quoting matches across these files.")
    else:
        parts.append("Empty quoting differs across these files.")
    return " ".join(parts)


def _habits_differ(header: Bucket, data: Bucket) -> bool:
    left = describe(header)
    right = describe(data)
    values_differ = (
        left.value_label not in ("no values",)
        and right.value_label not in ("no values",)
        and left.value_label != right.value_label
    )
    empties_differ = (
        left.empty_label != "none seen"
        and right.empty_label != "none seen"
        and left.empty_label != right.empty_label
    )
    return values_differ or empties_differ


def _value_habit(bucket: Bucket, quoted: int, bare: int) -> tuple[str, str]:
    if quoted == 0 and bare == 0:
        return "no values", "There are no non-empty values in the judged rows."
    if quoted == 0:
        return "bare", "Values are written without quotes."
    if bare == 0 and bucket.quoted_optional == 0 and bucket.quoted_required > 0:
        return (
            "when required",
            "Quotes appear only on values that contain the delimiter, a quote, or a line break.",
        )
    if bare == 0:
        return "always quoted", "Every non-empty value is wrapped in quotes."
    if _is_text_quoted(bucket):
        return (
            "text quoted",
            "Text values are wrapped in quotes. Numeric values are bare.",
        )
    if bucket.quoted_optional == 0 and bucket.quoted_required > 0:
        return (
            "when required",
            "Plain values are bare. Quotes are used only when a value contains the delimiter, a quote, or a line break.",
        )
    return (
        "mixed",
        f"Quoted values and bare values are both present "
        f"({quoted:,} quoted, {bare:,} bare), with no single rule that covers both.",
    )


def _empty_habit(bucket: Bucket) -> tuple[str, str]:
    quoted = bucket.empty_quoted
    bare = bucket.empty_bare
    if quoted == 0 and bare == 0:
        return (
            "none seen",
            "The judged rows have no empty values. "
            'A quoted empty ("") can be seen only on a row that has an empty field.',
        )
    if bare == 0:
        return "quoted (\"\")", 'Empty values are written as "".'
    if quoted == 0:
        return "bare", 'Empty values are left blank, rather than written as "".'
    return (
        "mixed",
        f'Some empty values are "" and some are blank '
        f"({quoted:,} quoted, {bare:,} bare).",
    )


def _caveat(bucket: Bucket, value_label: str) -> str:
    forced = (
        bucket.with_delim
        + bucket.with_break
        + bucket.with_inner_quote
        + bucket.bare_with_quote
    )
    if value_label == "bare" and forced == 0 and bucket.bare > 0:
        return (
            "No value contains the delimiter, a quote, or a line break, so a writer "
            "that quotes only in those cases would produce this same file."
        )
    if (
        value_label == "when required"
        and bucket.bare == 0
        and bucket.quoted_optional == 0
        and bucket.quoted_required > 0
    ):
        return (
            "Every value needed quotes. A writer that quotes every value would "
            "look the same on these rows."
        )
    return ""


def _is_text_quoted(bucket: Bucket) -> bool:
    # Plain text was quoted even though it did not need quotes, and every
    # bare value is numeric. Quotes that only protect a comma or a line
    # break are a different habit and are labeled "when required".
    return (
        bucket.quoted_optional > 0
        and bucket.text_quoted > 0
        and bucket.numeric_bare > 0
        and bucket.text_bare == 0
        and bucket.numeric_quoted == 0
    )


def _file_title(path: Path, index: int, total: int) -> str:
    name = path.name
    if total > 1:
        return f"  FILE {index} of {total}    {name}"
    return f"  FILE    {name}"


def _display_names(paths: list[Path]) -> list[str]:
    bases = [path.name for path in paths]
    if len(bases) == len(set(bases)):
        return bases
    return [f"{path.parent.name}/{path.name}" for path in paths]


def _fmt_delim(scan: Scan) -> str:
    names = {
        ",": "comma  ,",
        "|": "pipe  |",
        "\t": "tab",
        ";": "semicolon  ;",
    }
    label = names.get(scan.delim, repr(scan.delim))
    if scan.delim_status == "given":
        return f"{label}    given on the command line"
    if scan.delim_status == "guessed":
        return f"{label}    guessed, the file looks like one column"
    return label


def _fmt_quote(scan: Scan) -> str:
    mark = scan.quote
    if scan.quote_assumed:
        return f"{mark}    assumed, the file has no quotes"
    return mark


def _fmt_size(size: int) -> str:
    if size < 1024:
        return f"{size:,} B"
    value = float(size)
    for unit in ("KB", "MB", "GB", "TB"):
        value /= 1024
        if value < 1024 or unit == "TB":
            return f"{value:,.1f} {unit}"
    return f"{size:,} B"


def _fmt_seconds(seconds: float) -> str:
    if seconds < 10:
        return f"{seconds:.2f} s"
    if seconds < 60:
        return f"{seconds:.1f} s"
    minutes, sec = divmod(int(seconds), 60)
    return f"{minutes} min {sec} s"


def _count(label: str, value: int, *, sub: bool = False) -> str:
    if sub:
        label = f"    {label}"
    return f"    {label:<30} {value:>16,}"


def _kv(label: str, value: str) -> str:
    text = str(value)
    width = 22
    if len(text) <= 100:
        return f"  {label:<{width}} {text}"
    pad = " " * (width + 2)
    return f"  {label:<{width}}\n{pad}{text}"


def _wrap(text: str) -> str:
    return textwrap.fill(
        text,
        width=_WIDTH,
        initial_indent="  ",
        subsequent_indent="  ",
    )


def _table(headers: list[str], rows: list[list[str]]) -> str:
    widths = [len(header) for header in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))

    def draw(row: list[str]) -> str:
        cells = [cell.ljust(widths[index]) for index, cell in enumerate(row)]
        return "  " + "  ".join(cells)

    lines = [draw(headers), "  " + "  ".join("-" * width for width in widths)]
    lines.extend(draw(row) for row in rows)
    return "\n".join(lines)
