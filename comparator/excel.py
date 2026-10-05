"""Excel workbook in constant_memory mode (sections 13.2, 13.3, 14)."""

from __future__ import annotations

from pathlib import Path

import duckdb
import xlsxwriter

from .csvout import place_file
from .model import OutputInfo, PairResult, SheetInfo
from .settings import Settings

EXCEL_MAX_CELL = 32767
TRUNCATE_SUFFIX = "…[truncated]"


def _quote_val(value: str, enabled: bool) -> str:
    if not enabled:
        return value
    return f"'{value}'"


def _cell_text(value: object, truncate_counter: list[int]) -> str:
    if value is None:
        s = ""
    else:
        s = str(value)
    if len(s) > EXCEL_MAX_CELL:
        keep = max(0, EXCEL_MAX_CELL - len(TRUNCATE_SUFFIX))
        s = s[:keep] + TRUNCATE_SUFFIX
        truncate_counter[0] += 1
    return s


def _write_row(ws, row_idx: int, values: list, truncate_counter: list[int]) -> None:
    for col, v in enumerate(values):
        ws.write_string(row_idx, col, _cell_text(v, truncate_counter))


def _fetch_batches(con, query: str, batch: int):
    con.execute(query)
    while True:
        rows = con.fetchmany(batch)
        if not rows:
            break
        yield rows


def write_workbook(
    con: duckdb.DuckDBPyConnection | None,
    result: PairResult,
    settings: Settings,
    output_folder: Path,
) -> OutputInfo:
    output_folder.mkdir(parents=True, exist_ok=True)
    final_path = output_folder / f"{result.prefix}_comparison.xlsx"
    temp_path = output_folder / f".{result.prefix}_comparison.xlsx.tmp"
    if temp_path.exists():
        try:
            temp_path.unlink()
        except OSError:
            pass

    truncate_counter = [0]
    sheets: list[SheetInfo] = []

    wb = xlsxwriter.Workbook(
        str(temp_path),
        {"constant_memory": True, "strings_to_urls": False},
    )
    try:
        # Write analysis sheets first; Summary last so truncation count is known.
        if result.columns_match:
            if result.column_diffs:
                sheets.append(
                    _write_column_diffs(wb, result, settings, truncate_counter)
                )
            if con is not None and result.key and result.key.columns:
                sheets.append(
                    _write_differences_sample(
                        wb, con, result, settings, truncate_counter
                    )
                )
                sheets.append(
                    _write_unpaired_sample(
                        wb, con, result, settings, truncate_counter
                    )
                )
            elif (
                con is not None
                and result.key is not None
                and not result.key.columns
                and result.mismatches_table
            ):
                sheets.append(
                    _write_mismatches_sample(
                        wb, con, result, settings, truncate_counter
                    )
                )
            if con is not None and (
                result.ds.rejected_lines or result.infa.rejected_lines
            ):
                sheets.append(
                    _write_rejects_sample(wb, con, result, settings, truncate_counter)
                )

        result.excel_truncated_cells = truncate_counter[0]
        if truncate_counter[0]:
            w = f"Excel cells truncated: {truncate_counter[0]}"
            if w not in result.warnings:
                result.warnings.append(w)
        result.sheets = sheets
        _write_summary(wb, result, settings, truncate_counter)
        result.excel_truncated_cells = truncate_counter[0]
    finally:
        wb.close()

    placed, renamed = place_file(temp_path, final_path)
    return OutputInfo(path=placed, line_count=0, kind="xlsx", renamed=renamed)


def _write_summary(
    wb: xlsxwriter.Workbook,
    result: PairResult,
    settings: Settings,
    truncate_counter: list[int],
) -> None:
    ws = wb.add_worksheet("Summary")
    row = 0

    def put(*vals: object) -> None:
        nonlocal row
        _write_row(ws, row, ["" if v is None else v for v in vals], truncate_counter)
        row += 1

    put("Prefix", result.prefix)
    put("Overall result", result.overall)
    if result.error_message:
        put("Error", result.error_message)

    put("")
    put("Per file", "DataStage", "Informatica")
    put("File name", result.ds.name, result.infa.name)
    ds_enc = result.ds.encoding.label if result.ds.encoding else ""
    infa_enc = result.infa.encoding.label if result.infa.encoding else ""
    put("Encoding", ds_enc, infa_enc)

    def fmt_line(fi) -> str:
        if not fi.csv_format:
            return ""
        f = fi.csv_format
        base = (
            f"delim={f.delim!r} quote={f.quote!r} escape={f.escape!r} ({f.source})"
        )
        if f.note:
            base += f"; {f.note}"
        return base

    put("Delimiter / quote / escape", fmt_line(result.ds), fmt_line(result.infa))
    put("Data rows", result.ds.row_count, result.infa.row_count)
    put("Rejected lines", result.ds.rejected_lines, result.infa.rejected_lines)
    put("Distinct rows", result.ds.distinct_rows, result.infa.distinct_rows)
    put("Column count", result.ds.column_count, result.infa.column_count)

    put("")
    put("Columns match", "Yes" if result.columns_match else "No")
    if result.header_compare and not result.header_compare.match:
        put("Position", "DataStage header", "Informatica header", "Status")
        for p in result.header_compare.positions:
            put(p["position"], p["ds"], p["infa"], p["status"])

    rc = result.row_compare
    if rc:
        put("")
        put("Matched rows", rc.matched_rows)
        put("Rows only in DataStage", rc.only_ds)
        put("Rows only in Informatica", rc.only_infa)
        put("Extra copies in DataStage", rc.extra_ds)
        put("Extra copies in Informatica", rc.extra_infa)
        put("Match rate", f"{rc.match_rate:.2f}%")

    if result.warnings:
        put("")
        put("Warnings")
        for w in result.warnings:
            put("", w)

    key = result.key
    if key:
        put("")
        put("Key source", key.source)
        put("Key columns", ", ".join(key.columns) if key.columns else "(none)")
        if key.columns:
            put("Key uniqueness DataStage", key.uniqueness_ds)
            put("Key uniqueness Informatica", key.uniqueness_infa)
            if key.overlap is not None:
                put("Key overlap", f"{key.overlap * 100:.2f}%")
            put("Paired records", key.paired_records)
            put("Differing cells", key.differing_cells)
            for status, cnt in sorted(key.unpaired_by_status.items()):
                put(f"Unpaired: {status}", cnt)
            if key.duplicate_key_ds or key.duplicate_key_infa:
                put("Duplicate key rows DataStage", key.duplicate_key_ds)
                put("Duplicate key rows Informatica", key.duplicate_key_infa)
        if key.fallback_reason:
            put("Fallback reason", key.fallback_reason)

    put("")
    put("Outputs")
    for sh in result.sheets:
        if sh.truncated:
            put(
                sh.name,
                f"{sh.rows_shown:,} of {sh.rows_total:,} (truncated, full list in CSV)",
            )
        else:
            put(sh.name, f"{sh.rows_shown:,} of {sh.rows_total:,}")
        if sh.note:
            put("", sh.note)
    for o in result.outputs:
        if o.kind != "xlsx":
            put(o.path.name, f"{o.line_count:,} lines")

    put("")
    put(
        "Note",
        "Opening a CSV by double-clicking in Excel may change values on screen "
        "(leading zeros, long numbers). Use Data → From Text/CSV and set every "
        "column to Text. The file itself is correct.",
    )

    put("")
    put("Timing")
    for t in result.timings:
        put(t.name, f"{t.seconds:.3f}s")
    put("Total", f"{result.total_seconds:.3f}s")
    if result.memory_limit:
        put("DuckDB memory_limit", result.memory_limit)
    if result.threads:
        put("DuckDB threads", result.threads)
    if result.excel_truncated_cells:
        put("Excel cells truncated", result.excel_truncated_cells)


def _write_column_diffs(
    wb: xlsxwriter.Workbook,
    result: PairResult,
    settings: Settings,
    truncate_counter: list[int],
) -> SheetInfo:
    ws = wb.add_worksheet("Column differences")
    headers = [
        "Column",
        "DS type",
        "INFA type",
        "DS distinct",
        "INFA distinct",
        "Only in DS",
        "Only in INFA",
        "Paired records differing",
        "DS-only examples",
        "INFA-only examples",
        "DS integer",
        "DS decimal",
        "DS text",
        "INFA integer",
        "INFA decimal",
        "INFA text",
    ]
    _write_row(ws, 0, headers, truncate_counter)
    q = settings.excel_quote_values
    for i, cd in enumerate(result.column_diffs, start=1):
        ds_ex = ", ".join(_quote_val(x, q) for x in cd.ds_examples)
        infa_ex = ", ".join(_quote_val(x, q) for x in cd.infa_examples)
        _write_row(
            ws,
            i,
            [
                cd.name,
                cd.ds_type,
                cd.infa_type,
                cd.ds_distinct,
                cd.infa_distinct,
                cd.only_ds,
                cd.only_infa,
                cd.paired_differing,
                ds_ex,
                infa_ex,
                cd.ds_integer,
                cd.ds_decimal,
                cd.ds_text,
                cd.infa_integer,
                cd.infa_decimal,
                cd.infa_text,
            ],
            truncate_counter,
        )
    total = len(result.column_diffs)
    return SheetInfo(name="Column differences", rows_shown=total, rows_total=total)


def _write_differences_sample(
    wb: xlsxwriter.Workbook,
    con: duckdb.DuckDBPyConnection,
    result: PairResult,
    settings: Settings,
    truncate_counter: list[int],
) -> SheetInfo:
    ws = wb.add_worksheet("Differences (sample)")
    key = result.key
    assert key is not None
    limit = min(settings.excel_sample_rows, 1048575)
    q = settings.excel_quote_values

    headers = list(key.columns) + ["Column", "INFA", "DS", "INFA copies", "DS copies"]
    _write_row(ws, 0, headers, truncate_counter)

    total = int(
        con.execute(f"SELECT count(*) FROM {result.differences_table}").fetchone()[0]
    )
    if total == 0:
        _write_row(ws, 1, ["No differences"], truncate_counter)
        return SheetInfo(
            name="Differences (sample)",
            rows_shown=0,
            rows_total=0,
            note="No differences",
        )

    key_order = ", ".join(f"k{i}" for i in range(len(key.columns)))
    display = (
        result.header_compare.display_headers
        if result.header_compare
        else result.ds.headers_display
    )
    cases = " CASE diff_column "
    for i, h in enumerate(display):
        cases += f" WHEN '{h.replace(chr(39), chr(39) + chr(39))}' THEN {i}"
    cases += " ELSE 9999 END"

    key_aliases = ", ".join(f"k{i}" for i in range(len(key.columns)))
    query = f"""
        SELECT {key_aliases}, diff_column, infa_val, ds_val, infa_copies, ds_copies
        FROM {result.differences_table}
        ORDER BY {key_order}, {cases}
        LIMIT {limit}
    """
    row_idx = 1
    nk = len(key.columns)
    for batch in _fetch_batches(con, query, settings.fetch_batch_rows):
        for r in batch:
            vals = list(r[:nk]) + [
                r[nk],
                _quote_val(str(r[nk + 1]), q),
                _quote_val(str(r[nk + 2]), q),
                r[nk + 3],
                r[nk + 4],
            ]
            _write_row(ws, row_idx, vals, truncate_counter)
            row_idx += 1

    shown = row_idx - 1
    return SheetInfo(
        name="Differences (sample)",
        rows_shown=shown,
        rows_total=total,
        truncated=shown < total,
    )


def _write_row_sheet(
    wb: xlsxwriter.Workbook,
    sheet_name: str,
    con: duckdb.DuckDBPyConnection,
    table: str,
    display_headers: list[str],
    settings: Settings,
    truncate_counter: list[int],
) -> SheetInfo:
    ws = wb.add_worksheet(sheet_name)
    headers = ["Status", "DS copies", "INFA copies", "Unmatched copies"] + list(
        display_headers
    )
    _write_row(ws, 0, headers, truncate_counter)
    total = int(con.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
    if total == 0:
        _write_row(ws, 1, ["No differences"], truncate_counter)
        return SheetInfo(
            name=sheet_name, rows_shown=0, rows_total=0, note="No differences"
        )

    limit = min(settings.excel_sample_rows, 1048575)
    order_cols = ", ".join(f"c{i + 1}" for i in range(len(display_headers)))
    query = f"""
        SELECT status, ds_copies, infa_copies, unmatched_copies, {order_cols}
        FROM {table}
        ORDER BY {order_cols}, status
        LIMIT {limit}
    """
    row_idx = 1
    for batch in _fetch_batches(con, query, settings.fetch_batch_rows):
        for r in batch:
            _write_row(ws, row_idx, list(r), truncate_counter)
            row_idx += 1
    shown = row_idx - 1
    return SheetInfo(
        name=sheet_name,
        rows_shown=shown,
        rows_total=total,
        truncated=shown < total,
    )


def _write_unpaired_sample(wb, con, result, settings, truncate_counter) -> SheetInfo:
    headers = (
        result.header_compare.display_headers
        if result.header_compare
        else result.ds.headers_display
    )
    return _write_row_sheet(
        wb,
        "Unpaired rows (sample)",
        con,
        result.unpaired_table,
        headers,
        settings,
        truncate_counter,
    )


def _write_mismatches_sample(wb, con, result, settings, truncate_counter) -> SheetInfo:
    headers = (
        result.header_compare.display_headers
        if result.header_compare
        else result.ds.headers_display
    )
    return _write_row_sheet(
        wb,
        "Mismatches (sample)",
        con,
        result.mismatches_table,
        headers,
        settings,
        truncate_counter,
    )


def _write_rejects_sample(wb, con, result, settings, truncate_counter) -> SheetInfo:
    ws = wb.add_worksheet("Rejected lines (sample)")
    _write_row(
        ws,
        0,
        ["File", "Line", "Error type", "Error message", "Raw line"],
        truncate_counter,
    )
    parts = []
    for label, fi in (("DataStage", result.ds), ("Informatica", result.infa)):
        if fi.rejected_lines and fi.rejects_table:
            try:
                con.execute(f"SELECT 1 FROM {fi.rejects_table} LIMIT 1")
            except Exception:
                continue
            parts.append(
                f"""
                SELECT '{label}', line, error_type, error_message,
                       trim(csv_line, chr(10) || chr(13))
                FROM {fi.rejects_table}
                """
            )
    if not parts:
        _write_row(ws, 1, ["No differences"], truncate_counter)
        return SheetInfo(name="Rejected lines (sample)", rows_shown=0, rows_total=0)

    limit = min(settings.excel_sample_rows, 1048575)
    union = " UNION ALL ".join(parts)
    total = int(con.execute(f"SELECT count(*) FROM ({union})").fetchone()[0])
    query = f"SELECT * FROM ({union}) ORDER BY 1, 2 LIMIT {limit}"
    row_idx = 1
    for batch in _fetch_batches(con, query, settings.fetch_batch_rows):
        for r in batch:
            _write_row(ws, row_idx, list(r), truncate_counter)
            row_idx += 1
    shown = row_idx - 1
    return SheetInfo(
        name="Rejected lines (sample)",
        rows_shown=shown,
        rows_total=total,
        truncated=shown < total,
    )
