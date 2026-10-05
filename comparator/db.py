"""DuckDB connection, load, rejects, and count-only (DESIGN.md sections 7, 16.1)."""

from __future__ import annotations

import os
from pathlib import Path

import duckdb
import psutil

from .model import CsvFormat, FileInfo
from .settings import Settings


class MaxLineSizeError(RuntimeError):
    """Raised when max_line_size aborts the scan (not recorded in rejects)."""


def memory_limit_string(settings: Settings) -> str:
    available = psutil.virtual_memory().available
    share = available * settings.memory_share_of_available
    min_b = settings.memory_limit_min_gb * (1024**3)
    max_b = settings.memory_limit_max_gb * (1024**3)
    clamped = max(min_b, min(max_b, share))
    # DuckDB accepts e.g. '1GB'
    gb = clamped / (1024**3)
    # Prefer whole GB strings when close, else MB
    if abs(gb - round(gb)) < 0.05:
        return f"{int(round(gb))}GB"
    mb = int(clamped / (1024**2))
    return f"{max(mb, 1)}MB"


def thread_count(settings: Settings) -> int:
    if settings.threads is not None:
        return max(1, int(settings.threads))
    return min(4, os.cpu_count() or 1)


def open_pair_db(
    db_path: Path,
    temp_folder: Path,
    settings: Settings,
    *,
    threads_override: int | None = None,
) -> tuple[duckdb.DuckDBPyConnection, str, int]:
    temp_folder.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    # Also remove wal
    wal = Path(str(db_path) + ".wal")
    if wal.exists():
        wal.unlink()

    con = duckdb.connect(str(db_path))
    mem = memory_limit_string(settings)
    threads = threads_override if threads_override is not None else thread_count(settings)
    con.execute(f"SET memory_limit='{mem}'")
    con.execute(f"SET threads={threads}")
    con.execute("SET preserve_insertion_order=false")
    con.execute(f"SET temp_directory='{temp_folder.as_posix()}'")
    return con, mem, threads


def _col_names(n: int) -> list[str]:
    return [f"c{i}" for i in range(1, n + 1)]


def _hash_expr(cols: list[str]) -> str:
    # md5_number(concat(strlen(coalesce(c1,'')), ':', coalesce(c1,''), ...))
    parts: list[str] = []
    for c in cols:
        parts.append(f"strlen(coalesce({c}, ''))")
        parts.append(f"':'")
        parts.append(f"coalesce({c}, '')")
    concat_args = ", ".join(parts)
    return f"md5_number(concat({concat_args}))"


def _select_coalesced(cols: list[str]) -> str:
    return ", ".join(f"coalesce({c}, '') AS {c}" for c in cols)


def load_file(
    con: duckdb.DuckDBPyConnection,
    file_info: FileInfo,
    n_cols: int,
    settings: Settings,
    table_name: str,
    rejects_table: str,
    rejects_scan: str,
) -> None:
    """Load CSV into on-disk table with coalesce and length-prefixed hash."""
    assert file_info.encoding is not None
    assert file_info.csv_format is not None
    path = file_info.encoding.path_for_reader
    fmt = file_info.csv_format
    cols = _col_names(n_cols)
    names_sql = "[" + ", ".join(f"'{c}'" for c in cols) + "]"

    # Drop previous rejects if any
    con.execute(f"DROP TABLE IF EXISTS {rejects_table}")
    con.execute(f"DROP TABLE IF EXISTS {rejects_scan}")
    con.execute(f"DROP TABLE IF EXISTS {table_name}")

    read_csv_sql = f"""
        read_csv(
            ?,
            delim=?,
            quote=?,
            escape=?,
            header=true,
            names={names_sql},
            all_varchar=true,
            store_rejects=true,
            rejects_table='{rejects_table}',
            rejects_scan='{rejects_scan}',
            max_line_size={int(settings.max_line_size_bytes)},
            null_padding=false
        )
    """
    hash_expr = _hash_expr(cols)
    select_cols = _select_coalesced(cols)
    sql = f"""
        CREATE TABLE {table_name} AS
        SELECT {select_cols}, {hash_expr} AS h
        FROM {read_csv_sql}
    """
    try:
        con.execute(
            sql,
            [str(path), fmt.delim, fmt.quote, fmt.escape],
        )
    except Exception as e:
        msg = str(e)
        if "max_line_size" in msg.lower() or "maximum line size" in msg.lower():
            raise MaxLineSizeError(msg) from e
        raise

    file_info.table_name = table_name
    file_info.rejects_table = rejects_table
    file_info.row_count = int(con.execute(f"SELECT count(*) FROM {table_name}").fetchone()[0])
    file_info.distinct_rows = int(
        con.execute(f"SELECT count(DISTINCT h) FROM {table_name}").fetchone()[0]
    )
    file_info.rejected_lines = count_rejected_lines(con, rejects_table)


def count_rejected_lines(con: duckdb.DuckDBPyConnection, rejects_table: str) -> int:
    """Count distinct line numbers in the rejects table."""
    exists = con.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = ?",
        [rejects_table],
    ).fetchone()[0]
    if not exists:
        # DuckDB may use different catalog; try direct
        try:
            return int(
                con.execute(
                    f"SELECT count(DISTINCT line) FROM {rejects_table}"
                ).fetchone()[0]
            )
        except Exception:
            return 0
    return int(
        con.execute(f"SELECT count(DISTINCT line) FROM {rejects_table}").fetchone()[0]
    )


def count_only(
    con: duckdb.DuckDBPyConnection,
    file_info: FileInfo,
    n_cols: int,
    settings: Settings,
    table_name: str,
    rejects_table: str,
    rejects_scan: str,
) -> None:
    """Count data rows and rejects without keeping the full table for comparison."""
    assert file_info.encoding is not None
    assert file_info.csv_format is not None
    path = file_info.encoding.path_for_reader
    fmt = file_info.csv_format
    cols = _col_names(max(n_cols, 1))
    names_sql = "[" + ", ".join(f"'{c}'" for c in cols) + "]"

    con.execute(f"DROP TABLE IF EXISTS {rejects_table}")
    con.execute(f"DROP TABLE IF EXISTS {rejects_scan}")
    con.execute(f"DROP TABLE IF EXISTS {table_name}")

    read_csv_sql = f"""
        read_csv(
            ?,
            delim=?,
            quote=?,
            escape=?,
            header=true,
            names={names_sql},
            all_varchar=true,
            store_rejects=true,
            rejects_table='{rejects_table}',
            rejects_scan='{rejects_scan}',
            max_line_size={int(settings.max_line_size_bytes)},
            null_padding=false
        )
    """
    try:
        con.execute(
            f"CREATE TABLE {table_name} AS SELECT * FROM {read_csv_sql}",
            [str(path), fmt.delim, fmt.quote, fmt.escape],
        )
    except Exception as e:
        msg = str(e)
        if "max_line_size" in msg.lower() or "maximum line size" in msg.lower():
            raise MaxLineSizeError(msg) from e
        raise

    file_info.table_name = table_name
    file_info.rejects_table = rejects_table
    file_info.row_count = int(con.execute(f"SELECT count(*) FROM {table_name}").fetchone()[0])
    file_info.rejected_lines = count_rejected_lines(con, rejects_table)
    # Free space — drop data table after counting when columns differ
    con.execute(f"DROP TABLE IF EXISTS {table_name}")


def rejects_over_limit(file_info: FileInfo, settings: Settings) -> bool:
    # Lines ≈ data rows + rejected + 1 header; use data+rejected as denominator base
    total_lines = file_info.row_count + file_info.rejected_lines
    if total_lines <= 0:
        return file_info.rejected_lines > 0 and settings.reject_limit_percent <= 0
    pct = 100.0 * file_info.rejected_lines / total_lines
    return pct > settings.reject_limit_percent


def strip_reject_csv_line(raw: str | None) -> str:
    if raw is None:
        return ""
    s = raw
    if s.startswith("\n"):
        s = s[1:]
    if s.startswith("\r\n"):
        s = s[2:]
    elif s.startswith("\r"):
        s = s[1:]
    return s
