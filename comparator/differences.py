"""Pairing, cell-level differences, unpaired rows, full-row fallback (section 12)."""

from __future__ import annotations

import duckdb

from .model import FileInfo, KeyResult, RowCompareResult


def build_differences(
    con: duckdb.DuckDBPyConnection,
    ds: FileInfo,
    infa: FileInfo,
    display_headers: list[str],
    key: KeyResult,
    row_compare: RowCompareResult,
) -> tuple[str | None, str | None, str | None]:
    """Create differences / unpaired / mismatches tables.

    Returns (differences_table, unpaired_table, mismatches_table).
    """
    cmp = row_compare.cmp_table
    n = len(display_headers)
    all_cols = [f"c{i}" for i in range(1, n + 1)]

    if not key.columns:
        mismatches = _build_mismatches(
            con, ds, infa, display_headers, cmp, all_cols
        )
        return None, None, mismatches

    is_manual = key.source.startswith("Manual")
    return _build_with_key(
        con, ds, infa, display_headers, key, row_compare, all_cols, is_manual
    )


def _build_with_key(
    con: duckdb.DuckDBPyConnection,
    ds: FileInfo,
    infa: FileInfo,
    display_headers: list[str],
    key: KeyResult,
    row_compare: RowCompareResult,
    all_cols: list[str],
    is_manual: bool,
) -> tuple[str, str, None]:
    cmp = row_compare.cmp_table
    key_idxs = key.column_indexes
    key_cols = [f"c{i + 1}" for i in key_idxs]
    key_select = ", ".join(key_cols)
    n = len(display_headers)
    non_key = [i for i in range(n) if i not in set(key_idxs)]

    con.execute("DROP TABLE IF EXISTS ds_only_dist")
    con.execute("DROP TABLE IF EXISTS infa_only_dist")
    con.execute(
        f"""
        CREATE TABLE ds_only_dist AS
        SELECT t.*, c.dn AS ds_copies, c.inn AS infa_copies
        FROM {ds.table_name} t
        INNER JOIN {cmp} c ON t.h = c.h
        WHERE c.inn = 0
        QUALIFY row_number() OVER (PARTITION BY t.h ORDER BY {', '.join(all_cols)}) = 1
        """
    )
    con.execute(
        f"""
        CREATE TABLE infa_only_dist AS
        SELECT t.*, c.dn AS ds_copies, c.inn AS infa_copies
        FROM {infa.table_name} t
        INNER JOIN {cmp} c ON t.h = c.h
        WHERE c.dn = 0
        QUALIFY row_number() OVER (PARTITION BY t.h ORDER BY {', '.join(all_cols)}) = 1
        """
    )

    unpaired_parts: list[str] = []

    if is_manual:
        con.execute("DROP TABLE IF EXISTS ds_dup_keys")
        con.execute("DROP TABLE IF EXISTS infa_dup_keys")
        con.execute(
            f"""
            CREATE TABLE ds_dup_keys AS
            SELECT {key_select} FROM ds_only_dist
            GROUP BY {key_select} HAVING count(*) > 1
            """
        )
        con.execute(
            f"""
            CREATE TABLE infa_dup_keys AS
            SELECT {key_select} FROM infa_only_dist
            GROUP BY {key_select} HAVING count(*) > 1
            """
        )
        key.duplicate_key_ds = int(
            con.execute(
                f"""
                SELECT count(*) FROM ds_only_dist d
                WHERE EXISTS (
                    SELECT 1 FROM ds_dup_keys k
                    WHERE {' AND '.join(f'd.{c} = k.{c}' for c in key_cols)}
                )
                """
            ).fetchone()[0]
        )
        key.duplicate_key_infa = int(
            con.execute(
                f"""
                SELECT count(*) FROM infa_only_dist i
                WHERE EXISTS (
                    SELECT 1 FROM infa_dup_keys k
                    WHERE {' AND '.join(f'i.{c} = k.{c}' for c in key_cols)}
                )
                """
            ).fetchone()[0]
        )

        dup_ds_select = _unpaired_select(
            "Duplicate key in DataStage",
            "d",
            display_headers,
            ds_copies="d.ds_copies",
            infa_copies="0",
            unmatched="d.ds_copies",
        )
        unpaired_parts.append(
            f"""
            SELECT {dup_ds_select}
            FROM ds_only_dist d
            WHERE EXISTS (
                SELECT 1 FROM ds_dup_keys k
                WHERE {' AND '.join(f'd.{c} = k.{c}' for c in key_cols)}
            )
            """
        )
        dup_infa_select = _unpaired_select(
            "Duplicate key in Informatica",
            "i",
            display_headers,
            ds_copies="0",
            infa_copies="i.infa_copies",
            unmatched="i.infa_copies",
        )
        unpaired_parts.append(
            f"""
            SELECT {dup_infa_select}
            FROM infa_only_dist i
            WHERE EXISTS (
                SELECT 1 FROM infa_dup_keys k
                WHERE {' AND '.join(f'i.{c} = k.{c}' for c in key_cols)}
            )
            """
        )

        con.execute("DROP TABLE IF EXISTS ds_pairable")
        con.execute("DROP TABLE IF EXISTS infa_pairable")
        con.execute(
            f"""
            CREATE TABLE ds_pairable AS
            SELECT d.* FROM ds_only_dist d
            WHERE NOT EXISTS (
                SELECT 1 FROM ds_dup_keys k
                WHERE {' AND '.join(f'd.{c} = k.{c}' for c in key_cols)}
            )
            """
        )
        con.execute(
            f"""
            CREATE TABLE infa_pairable AS
            SELECT i.* FROM infa_only_dist i
            WHERE NOT EXISTS (
                SELECT 1 FROM infa_dup_keys k
                WHERE {' AND '.join(f'i.{c} = k.{c}' for c in key_cols)}
            )
            """
        )
    else:
        con.execute("DROP TABLE IF EXISTS ds_pairable")
        con.execute("DROP TABLE IF EXISTS infa_pairable")
        con.execute("CREATE TABLE ds_pairable AS SELECT * FROM ds_only_dist")
        con.execute("CREATE TABLE infa_pairable AS SELECT * FROM infa_only_dist")

    join_cond = " AND ".join(f"d.{c} = i.{c}" for c in key_cols)
    con.execute("DROP TABLE IF EXISTS paired")
    con.execute(
        f"""
        CREATE TABLE paired AS
        SELECT d.*,
               {', '.join(f'i.c{j + 1} AS infa_c{j + 1}' for j in range(n))},
               i.infa_copies AS i_infa_copies
        FROM ds_pairable d
        INNER JOIN infa_pairable i ON {join_cond}
        """
    )

    key_out_cols = ", ".join(f"p.c{idx + 1} AS k{k}" for k, idx in enumerate(key_idxs))
    diff_parts: list[str] = []
    for col_i in non_key:
        c = f"c{col_i + 1}"
        ic = f"infa_c{col_i + 1}"
        col_name = display_headers[col_i].replace("'", "''")
        diff_parts.append(
            f"""
            SELECT {key_out_cols},
                   '{col_name}' AS diff_column,
                   p.{ic} AS infa_val,
                   p.{c} AS ds_val,
                   p.i_infa_copies AS infa_copies,
                   p.ds_copies AS ds_copies
            FROM paired p
            WHERE p.{c} <> p.{ic}
            """
        )

    con.execute("DROP TABLE IF EXISTS differences")
    if diff_parts:
        con.execute("CREATE TABLE differences AS " + " UNION ALL ".join(diff_parts))
    else:
        key_defs = ", ".join(f"k{k} VARCHAR" for k in range(len(key_idxs)))
        con.execute(
            f"""
            CREATE TABLE differences (
                {key_defs},
                diff_column VARCHAR,
                infa_val VARCHAR,
                ds_val VARCHAR,
                infa_copies BIGINT,
                ds_copies BIGINT
            )
            """
        )

    key.differing_cells = int(
        con.execute("SELECT count(*) FROM differences").fetchone()[0]
    )
    key.paired_records = int(con.execute("SELECT count(*) FROM paired").fetchone()[0])

    only_ds_sel = _unpaired_select(
        "Only in DataStage",
        "d",
        display_headers,
        ds_copies="d.ds_copies",
        infa_copies="0",
        unmatched="d.ds_copies",
    )
    unpaired_parts.append(
        f"""
        SELECT {only_ds_sel}
        FROM ds_pairable d
        WHERE NOT EXISTS (
            SELECT 1 FROM paired p
            WHERE {' AND '.join(f'd.{c} = p.{c}' for c in key_cols)}
        )
        """
    )
    only_infa_sel = _unpaired_select(
        "Only in Informatica",
        "i",
        display_headers,
        ds_copies="0",
        infa_copies="i.infa_copies",
        unmatched="i.infa_copies",
    )
    unpaired_parts.append(
        f"""
        SELECT {only_infa_sel}
        FROM infa_pairable i
        WHERE NOT EXISTS (
            SELECT 1 FROM paired p
            WHERE {' AND '.join(f'i.{c} = p.{c}' for c in key_cols)}
        )
        """
    )

    extra_ds_sel = _unpaired_select(
        "Extra copies in DataStage",
        "t",
        display_headers,
        ds_copies="c.dn",
        infa_copies="c.inn",
        unmatched="(c.dn - c.inn)",
    )
    unpaired_parts.append(
        f"""
        SELECT {extra_ds_sel}
        FROM {ds.table_name} t
        INNER JOIN {cmp} c ON t.h = c.h
        WHERE c.dn > c.inn AND c.inn > 0
        QUALIFY row_number() OVER (PARTITION BY t.h ORDER BY {', '.join(all_cols)}) = 1
        """
    )
    extra_infa_sel = _unpaired_select(
        "Extra copies in Informatica",
        "t",
        display_headers,
        ds_copies="c.dn",
        infa_copies="c.inn",
        unmatched="(c.inn - c.dn)",
    )
    unpaired_parts.append(
        f"""
        SELECT {extra_infa_sel}
        FROM {infa.table_name} t
        INNER JOIN {cmp} c ON t.h = c.h
        WHERE c.inn > c.dn AND c.dn > 0
        QUALIFY row_number() OVER (PARTITION BY t.h ORDER BY {', '.join(all_cols)}) = 1
        """
    )

    con.execute("DROP TABLE IF EXISTS unpaired")
    con.execute("CREATE TABLE unpaired AS " + " UNION ALL ".join(unpaired_parts))

    status_rows = con.execute(
        "SELECT status, count(*) FROM unpaired GROUP BY status"
    ).fetchall()
    key.unpaired_by_status = {str(s): int(c) for s, c in status_rows}

    return "differences", "unpaired", None


def _unpaired_select(
    status: str,
    alias: str,
    display_headers: list[str],
    *,
    ds_copies: str,
    infa_copies: str,
    unmatched: str,
) -> str:
    cols = ", ".join(
        f"{alias}.c{i + 1} AS c{i + 1}" for i in range(len(display_headers))
    )
    return (
        f"'{status}' AS status, "
        f"{ds_copies} AS ds_copies, "
        f"{infa_copies} AS infa_copies, "
        f"{unmatched} AS unmatched_copies, "
        f"{cols}"
    )


def _build_mismatches(
    con: duckdb.DuckDBPyConnection,
    ds: FileInfo,
    infa: FileInfo,
    display_headers: list[str],
    cmp: str,
    all_cols: list[str],
) -> str:
    parts: list[str] = []
    specs = [
        ("Only in DataStage", ds.table_name, "c.inn = 0", "c.dn", "0", "c.dn"),
        ("Only in Informatica", infa.table_name, "c.dn = 0", "0", "c.inn", "c.inn"),
        (
            "Extra copies in DataStage",
            ds.table_name,
            "c.dn > c.inn AND c.inn > 0",
            "c.dn",
            "c.inn",
            "(c.dn - c.inn)",
        ),
        (
            "Extra copies in Informatica",
            infa.table_name,
            "c.inn > c.dn AND c.dn > 0",
            "c.dn",
            "c.inn",
            "(c.inn - c.dn)",
        ),
    ]
    for status, table, where, ds_c, infa_c, unmatched in specs:
        sel = _unpaired_select(
            status,
            "t",
            display_headers,
            ds_copies=ds_c,
            infa_copies=infa_c,
            unmatched=unmatched,
        )
        parts.append(
            f"""
            SELECT {sel}
            FROM {table} t
            INNER JOIN {cmp} c ON t.h = c.h
            WHERE {where}
            QUALIFY row_number() OVER (PARTITION BY t.h ORDER BY {', '.join(all_cols)}) = 1
            """
        )
    con.execute("DROP TABLE IF EXISTS mismatches")
    con.execute("CREATE TABLE mismatches AS " + " UNION ALL ".join(parts))
    return "mismatches"
