"""Startup self-test of DuckDB reader settings (DESIGN.md section 7.4)."""

from __future__ import annotations

from pathlib import Path

import duckdb

from .db import open_pair_db
from .settings import Settings

# Built-in CSV covering edge cases. Exact expected values after coalesce.
_SELFTEST_CSV = (
    "c1,c2,c3,c4\n"
    "  lead,trail  ,,\n"  # leading space, trailing space, empty, empty — wait need space-only
)

# Better construct carefully:
# Columns: a,b,c,d,e,f,g,h
# Row covering:
# - leading/trailing spaces
# - empty field, quoted empty, space-only
# - leading/trailing zeros
# - delimiter inside quoted field
# - line break inside quoted field
# - doubled quote
# - non-ASCII


def _build_selftest_bytes() -> bytes:
    # header + one data row with quoted newline
    # a= leading space " lead"
    # b= trailing "trail "
    # c= empty
    # d= ""
    # e= single space
    # f= 0000000100.00
    # g= 10.000
    # h= "a,b"
    # i= "line1\nline2"
    # j= "say ""hi"""
    # k= café
    lines = [
        "a,b,c,d,e,f,g,h,i,j,k",
        ' lead,trail ,,"", ,0000000100.00,10.000,"a,b","line1\nline2","say ""hi""",café',
    ]
    return ("\n".join(lines) + "\n").encode("utf-8")


_EXPECTED = [
    " lead",
    "trail ",
    "",
    "",
    " ",
    "0000000100.00",
    "10.000",
    "a,b",
    "line1\nline2",
    'say "hi"',
    "café",
]


def run_selftest(settings: Settings, temp_folder: Path | None = None) -> None:
    """Write a tiny CSV, load with production settings, assert cell values.

    Raises RuntimeError on failure (stops the run before real files).
    """
    temp = Path(temp_folder or settings.temp_folder)
    temp.mkdir(parents=True, exist_ok=True)
    csv_path = temp / "_selftest.csv"
    db_path = temp / "_selftest.duckdb"
    csv_path.write_bytes(_build_selftest_bytes())

    n = len(_EXPECTED)
    cols = [f"c{i}" for i in range(1, n + 1)]
    names_sql = "[" + ", ".join(f"'{c}'" for c in cols) + "]"
    select_cols = ", ".join(f"coalesce({c}, '') AS {c}" for c in cols)
    parts: list[str] = []
    for c in cols:
        parts.append(f"strlen(coalesce({c}, ''))")
        parts.append("':'")
        parts.append(f"coalesce({c}, '')")
    hash_expr = f"md5_number(concat({', '.join(parts)}))"

    con = None
    try:
        con, _, _ = open_pair_db(db_path, temp, settings)
        sql = f"""
            CREATE TABLE selftest AS
            SELECT {select_cols}, {hash_expr} AS h
            FROM read_csv(
                ?,
                delim=',',
                quote='"',
                escape='"',
                header=true,
                names={names_sql},
                all_varchar=true,
                store_rejects=true,
                rejects_table='selftest_rejects',
                rejects_scan='selftest_scans',
                max_line_size={int(settings.max_line_size_bytes)},
                null_padding=false
            )
        """
        con.execute(sql, [str(csv_path)])
        row = con.execute(
            f"SELECT {', '.join(cols)} FROM selftest"
        ).fetchone()
        if row is None:
            raise RuntimeError("Self-test failed: no row loaded")
        for i, (got, exp) in enumerate(zip(row, _EXPECTED)):
            if got != exp:
                raise RuntimeError(
                    f"Self-test failed on column c{i + 1}: "
                    f"expected {exp!r}, got {got!r}"
                )
        # Ensure hash is present and non-null
        h = con.execute("SELECT h FROM selftest").fetchone()[0]
        if h is None:
            raise RuntimeError("Self-test failed: hash is NULL")
    finally:
        if con is not None:
            con.close()
        for p in (csv_path, db_path, Path(str(db_path) + ".wal")):
            try:
                if p.exists():
                    p.unlink()
            except OSError:
                pass
