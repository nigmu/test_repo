"""Row comparison on hashes (DESIGN.md section 9)."""

from __future__ import annotations

import duckdb

from .model import FileInfo, RowCompareResult


def compare_rows(
    con: duckdb.DuckDBPyConnection,
    ds: FileInfo,
    infa: FileInfo,
    cmp_table: str = "cmp",
) -> RowCompareResult:
    """Group by hash, full outer join, compute all section 9 counts."""
    con.execute(f"DROP TABLE IF EXISTS {cmp_table}")
    con.execute(
        f"""
        CREATE TABLE {cmp_table} AS
        SELECT
            coalesce(d.h, i.h) AS h,
            coalesce(d.n, 0) AS dn,
            coalesce(i.n, 0) AS inn
        FROM (
            SELECT h, count(*) AS n FROM {ds.table_name} GROUP BY h
        ) d
        FULL OUTER JOIN (
            SELECT h, count(*) AS n FROM {infa.table_name} GROUP BY h
        ) i ON d.h = i.h
        """
    )

    row = con.execute(
        f"""
        SELECT
            coalesce(sum(least(dn, inn)), 0),
            coalesce(sum(CASE WHEN inn = 0 THEN dn ELSE 0 END), 0),
            coalesce(sum(CASE WHEN dn = 0 THEN inn ELSE 0 END), 0),
            coalesce(sum(CASE WHEN dn > inn AND inn > 0 THEN dn - inn ELSE 0 END), 0),
            coalesce(sum(CASE WHEN inn > dn AND dn > 0 THEN inn - dn ELSE 0 END), 0),
            coalesce(sum(CASE WHEN dn > 0 THEN 1 ELSE 0 END), 0),
            coalesce(sum(CASE WHEN inn > 0 THEN 1 ELSE 0 END), 0)
        FROM {cmp_table}
        """
    ).fetchone()

    matched = int(row[0])
    only_ds = int(row[1])
    only_infa = int(row[2])
    extra_ds = int(row[3])
    extra_infa = int(row[4])
    distinct_ds = int(row[5])
    distinct_infa = int(row[6])

    larger = max(ds.row_count, infa.row_count)
    match_rate = (100.0 * matched / larger) if larger else 0.0

    return RowCompareResult(
        matched_rows=matched,
        only_ds=only_ds,
        only_infa=only_infa,
        extra_ds=extra_ds,
        extra_infa=extra_infa,
        distinct_ds=distinct_ds,
        distinct_infa=distinct_infa,
        match_rate=match_rate,
        cmp_table=cmp_table,
    )
