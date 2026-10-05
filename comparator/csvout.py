"""CSV outputs via DuckDB COPY and safe file placement (section 13.1)."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import duckdb

from .db import strip_reject_csv_line
from .model import FileInfo, KeyResult, OutputInfo, PairResult


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def place_file(temp_path: Path, final_path: Path) -> tuple[Path, bool]:
    """os.replace temp → final; on lock, use timestamped name."""
    try:
        os.replace(str(temp_path), str(final_path))
        return final_path, False
    except OSError:
        stem = final_path.stem
        suffix = final_path.suffix
        alt = final_path.with_name(f"{stem}_{_timestamp()}{suffix}")
        os.replace(str(temp_path), str(alt))
        return alt, True


def copy_query_to_csv(
    con: duckdb.DuckDBPyConnection,
    query: str,
    output_folder: Path,
    final_name: str,
) -> OutputInfo:
    output_folder.mkdir(parents=True, exist_ok=True)
    final_path = output_folder / final_name
    temp_path = output_folder / f".{final_name}.tmp"
    if temp_path.exists():
        temp_path.unlink()
    # HEADER true, comma delimiter — DuckDB COPY defaults
    con.execute(
        f"COPY ({query}) TO ? (HEADER, DELIMITER ',', QUOTE '\"')",
        [str(temp_path)],
    )
    placed, renamed = place_file(temp_path, final_path)
    # Count lines (data rows = total lines - 1 if header present)
    line_count = 0
    with placed.open("rb") as f:
        for _ in f:
            line_count += 1
    data_lines = max(0, line_count - 1)
    return OutputInfo(path=placed, line_count=data_lines, kind="csv", renamed=renamed)


def write_differences_csv(
    con: duckdb.DuckDBPyConnection,
    result: PairResult,
    key: KeyResult,
) -> OutputInfo | None:
    if not result.differences_table:
        return None
    n = con.execute(f"SELECT count(*) FROM {result.differences_table}").fetchone()[0]
    if n == 0:
        return None
    # Build output column names: key columns, Column, INFA, DS, INFA copies, DS copies
    key_aliases = ", ".join(
        f'k{i} AS "{col}"' for i, col in enumerate(key.columns)
    )
    query = f"""
        SELECT {key_aliases},
               diff_column AS "Column",
               infa_val AS "INFA",
               ds_val AS "DS",
               infa_copies AS "INFA copies",
               ds_copies AS "DS copies"
        FROM {result.differences_table}
    """
    out = copy_query_to_csv(
        con, query, result.ds.path.parent.parent.parent / "x"
        if False
        else Path("."),  # placeholder replaced by caller path
        f"{result.prefix}_comparison_differences.csv",
    )
    return out


def write_differences_csv_to(
    con: duckdb.DuckDBPyConnection,
    output_folder: Path,
    prefix: str,
    differences_table: str,
    key_columns: list[str],
) -> OutputInfo | None:
    n = con.execute(f"SELECT count(*) FROM {differences_table}").fetchone()[0]
    if n == 0:
        return None
    key_aliases = ", ".join(
        f'k{i} AS "{_sql_ident(col)}"' for i, col in enumerate(key_columns)
    )
    query = f"""
        SELECT {key_aliases},
               diff_column AS "Column",
               infa_val AS "INFA",
               ds_val AS "DS",
               infa_copies AS "INFA copies",
               ds_copies AS "DS copies"
        FROM {differences_table}
    """
    info = copy_query_to_csv(
        con, query, output_folder, f"{prefix}_comparison_differences.csv"
    )
    info.kind = "differences"
    return info


def write_unpaired_csv_to(
    con: duckdb.DuckDBPyConnection,
    output_folder: Path,
    prefix: str,
    unpaired_table: str,
    display_headers: list[str],
) -> OutputInfo | None:
    n = con.execute(f"SELECT count(*) FROM {unpaired_table}").fetchone()[0]
    if n == 0:
        return None
    data_cols = ", ".join(
        f'c{i + 1} AS "{_sql_ident(h)}"' for i, h in enumerate(display_headers)
    )
    query = f"""
        SELECT status AS "Status",
               ds_copies AS "DS copies",
               infa_copies AS "INFA copies",
               unmatched_copies AS "Unmatched copies",
               {data_cols}
        FROM {unpaired_table}
    """
    info = copy_query_to_csv(
        con, query, output_folder, f"{prefix}_comparison_unpaired.csv"
    )
    info.kind = "unpaired"
    return info


def write_mismatches_csv_to(
    con: duckdb.DuckDBPyConnection,
    output_folder: Path,
    prefix: str,
    mismatches_table: str,
    display_headers: list[str],
) -> OutputInfo | None:
    n = con.execute(f"SELECT count(*) FROM {mismatches_table}").fetchone()[0]
    if n == 0:
        return None
    data_cols = ", ".join(
        f'c{i + 1} AS "{_sql_ident(h)}"' for i, h in enumerate(display_headers)
    )
    query = f"""
        SELECT status AS "Status",
               ds_copies AS "DS copies",
               infa_copies AS "INFA copies",
               unmatched_copies AS "Unmatched copies",
               {data_cols}
        FROM {mismatches_table}
    """
    info = copy_query_to_csv(
        con, query, output_folder, f"{prefix}_comparison_mismatches.csv"
    )
    info.kind = "mismatches"
    return info


def write_rejected_csv_to(
    con: duckdb.DuckDBPyConnection,
    output_folder: Path,
    prefix: str,
    ds: FileInfo,
    infa: FileInfo,
) -> OutputInfo | None:
    parts: list[str] = []
    for side, fi in (("DataStage", ds), ("Informatica", infa)):
        if not fi.rejects_table or fi.rejected_lines == 0:
            continue
        # Check table exists
        try:
            con.execute(f"SELECT 1 FROM {fi.rejects_table} LIMIT 1")
        except Exception:
            continue
        parts.append(
            f"""
            SELECT
                '{side}' AS "File",
                line AS "Line",
                error_type AS "Error type",
                error_message AS "Error message",
                trim(csv_line, chr(10) || chr(13)) AS "Raw line"
            FROM {fi.rejects_table}
            """
        )
    if not parts:
        return None
    # Deduplicate by file+line for the full list? Design says every rejected line
    # — one entry per reject row; but Summary counts distinct lines.
    # Sheet/CSV lists reject entries. Keep all reject rows but we could DISTINCT ON line.
    # Design: "Every rejected line" with line number — use distinct lines, first error.
    query = " UNION ALL ".join(parts)
    # Actually list all reject rows; for distinct line display we sample uniquely in excel
    info = copy_query_to_csv(
        con, query, output_folder, f"{prefix}_comparison_rejected.csv"
    )
    info.kind = "rejected"
    return info


def _sql_ident(name: str) -> str:
    return name.replace('"', '""')
