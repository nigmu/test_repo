"""Column differences and type labels (DESIGN.md section 10)."""

from __future__ import annotations

import duckdb

from .model import ColumnDiff, FileInfo
from .settings import Settings

# Integer-like: optional -, then digits
_INTEGER_RE = r'^-?[0-9]+$'
# Decimal-like: optional -, digits with one ., at least one digit before or after
_DECIMAL_RE = r'^-?([0-9]+\.[0-9]*|\.[0-9]+)$'


def analyze_columns(
    con: duckdb.DuckDBPyConnection,
    ds: FileInfo,
    infa: FileInfo,
    display_headers: list[str],
    settings: Settings,
    cmp_table: str = "cmp",
) -> list[ColumnDiff]:
    n = len(display_headers)
    example_n = settings.example_values
    results: list[ColumnDiff] = []

    # Type counts: one scan per file for all columns
    ds_types = _type_counts(con, ds.table_name, n)
    infa_types = _type_counts(con, infa.table_name, n)

    distinct_row_ds = ds.distinct_rows
    distinct_row_infa = infa.distinct_rows

    for i in range(n):
        col = f"c{i + 1}"
        name = display_headers[i]

        # Distinct value comparison
        con.execute(f"DROP TABLE IF EXISTS _ds_vals")
        con.execute(f"DROP TABLE IF EXISTS _infa_vals")
        con.execute(
            f"CREATE TEMP TABLE _ds_vals AS SELECT DISTINCT {col} AS v FROM {ds.table_name}"
        )
        con.execute(
            f"CREATE TEMP TABLE _infa_vals AS SELECT DISTINCT {col} AS v FROM {infa.table_name}"
        )

        ds_distinct = int(con.execute("SELECT count(*) FROM _ds_vals").fetchone()[0])
        infa_distinct = int(con.execute("SELECT count(*) FROM _infa_vals").fetchone()[0])
        only_ds = int(
            con.execute(
                "SELECT count(*) FROM _ds_vals d LEFT JOIN _infa_vals i ON d.v = i.v WHERE i.v IS NULL"
            ).fetchone()[0]
        )
        only_infa = int(
            con.execute(
                "SELECT count(*) FROM _infa_vals i LEFT JOIN _ds_vals d ON i.v = d.v WHERE d.v IS NULL"
            ).fetchone()[0]
        )
        both = int(
            con.execute(
                "SELECT count(*) FROM _ds_vals d INNER JOIN _infa_vals i ON d.v = i.v"
            ).fetchone()[0]
        )
        larger = max(ds_distinct, infa_distinct)
        overlap = (both / larger) if larger else 0.0

        ds_examples = [
            r[0]
            for r in con.execute(
                f"""
                SELECT d.v FROM _ds_vals d
                LEFT JOIN _infa_vals i ON d.v = i.v
                WHERE i.v IS NULL
                ORDER BY d.v
                LIMIT {example_n}
                """
            ).fetchall()
        ]
        infa_examples = [
            r[0]
            for r in con.execute(
                f"""
                SELECT i.v FROM _infa_vals i
                LEFT JOIN _ds_vals d ON i.v = d.v
                WHERE d.v IS NULL
                ORDER BY i.v
                LIMIT {example_n}
                """
            ).fetchall()
        ]

        di, dd, dt = ds_types[i]
        ii, idc, it = infa_types[i]
        ds_label = _column_label(di, dd, dt)
        infa_label = _column_label(ii, idc, it)

        unique_ds = ds_distinct == distinct_row_ds and distinct_row_ds > 0
        unique_infa = infa_distinct == distinct_row_infa and distinct_row_infa > 0

        results.append(
            ColumnDiff(
                name=name,
                position=i + 1,
                ds_type=ds_label,
                infa_type=infa_label,
                ds_integer=di,
                ds_decimal=dd,
                ds_text=dt,
                infa_integer=ii,
                infa_decimal=idc,
                infa_text=it,
                ds_distinct=ds_distinct,
                infa_distinct=infa_distinct,
                only_ds=only_ds,
                only_infa=only_infa,
                ds_examples=ds_examples,
                infa_examples=infa_examples,
                paired_differing=0,
                unique_ds=unique_ds,
                unique_infa=unique_infa,
                overlap=overlap,
            )
        )

    return results


def _type_counts(
    con: duckdb.DuckDBPyConnection, table: str, n: int
) -> list[tuple[int, int, int]]:
    parts: list[str] = []
    for i in range(1, n + 1):
        c = f"c{i}"
        parts.append(
            f"count_if({c} <> '' AND regexp_full_match({c}, '{_INTEGER_RE}'))"
        )
        parts.append(
            f"count_if({c} <> '' AND regexp_full_match({c}, '{_DECIMAL_RE}'))"
        )
        parts.append(
            f"count_if({c} <> '' AND NOT regexp_full_match({c}, '{_INTEGER_RE}') "
            f"AND NOT regexp_full_match({c}, '{_DECIMAL_RE}'))"
        )
    row = con.execute(f"SELECT {', '.join(parts)} FROM {table}").fetchone()
    out: list[tuple[int, int, int]] = []
    for i in range(n):
        out.append((int(row[i * 3]), int(row[i * 3 + 1]), int(row[i * 3 + 2])))
    return out


def _column_label(integer: int, decimal: int, text: int) -> str:
    kinds = []
    if integer:
        kinds.append("Integer-like")
    if decimal:
        kinds.append("Decimal-like")
    if text:
        kinds.append("Text")
    if not kinds:
        return "Empty"
    if len(kinds) == 1:
        return kinds[0]
    return "Mixed"


def update_paired_differing(
    con: duckdb.DuckDBPyConnection,
    column_diffs: list[ColumnDiff],
    differences_table: str | None,
    key_indexes: list[int],
) -> None:
    """Fill paired_differing counts; key columns get the label 'Key'."""
    key_set = set(key_indexes)
    for i, cd in enumerate(column_diffs):
        if i in key_set:
            cd.paired_differing = "Key"
            continue
        if not differences_table:
            cd.paired_differing = 0
            continue
        # One differences row per paired record that differs in this column
        n = con.execute(
            f"SELECT count(*) FROM {differences_table} WHERE diff_column = ?",
            [cd.name],
        ).fetchone()[0]
        cd.paired_differing = int(n)
