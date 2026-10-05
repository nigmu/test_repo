"""Key selection: manual, automatic, or none (DESIGN.md section 11)."""

from __future__ import annotations

import itertools
import re

import duckdb

from .model import ColumnDiff, FileInfo, KeyResult
from .settings import Settings


def _normalize(name: str) -> str:
    return name.strip().casefold()


def _header_matches_hint(header: str, hints: list[str]) -> bool:
    # Match hint as a word in the header
    words = re.findall(r"[A-Za-z0-9]+", header)
    upper_words = {w.upper() for w in words}
    return any(h.upper() in upper_words for h in hints)


def resolve_manual_key(
    names: list[str], display_headers: list[str]
) -> tuple[list[int], str | None]:
    """Map key column names to indexes. Returns (indexes, error_reason)."""
    indexes: list[int] = []
    for name in names:
        matches = [
            i
            for i, h in enumerate(display_headers)
            if _normalize(h) == _normalize(name)
            or _normalize(h.split(" (")[0]) == _normalize(name)
        ]
        # Also match against raw-like display without position suffix carefully
        if not matches:
            # try matching base name ignoring position suffix pattern
            matches = [
                i
                for i, h in enumerate(display_headers)
                if _normalize(re.sub(r" \(\d+\)$", "", h)) == _normalize(name)
            ]
        if len(matches) == 0:
            return [], f"Key column {name!r} matches no header"
        if len(matches) > 1:
            return [], f"Key column {name!r} matches more than one header"
        indexes.append(matches[0])
    return indexes, None


def select_key(
    con: duckdb.DuckDBPyConnection,
    prefix: str,
    ds: FileInfo,
    infa: FileInfo,
    display_headers: list[str],
    column_diffs: list[ColumnDiff],
    settings: Settings,
) -> KeyResult:
    overrides = settings.key_overrides or {}
    # Case-insensitive prefix lookup
    override_val = None
    has_override = False
    for k, v in overrides.items():
        if k.casefold() == prefix.casefold():
            override_val = v
            has_override = True
            break

    if has_override:
        if override_val is None:
            return KeyResult(
                source="None",
                fallback_reason="KEY_OVERRIDES set to None (full-row output)",
            )
        indexes, err = resolve_manual_key(list(override_val), display_headers)
        if err:
            return KeyResult(
                source="Manual (configs.py)",
                invalid=True,
                invalid_reason=err,
            )
        cols = [display_headers[i] for i in indexes]
        stats = _key_stats(con, ds, infa, indexes)
        return KeyResult(
            source="Manual (configs.py)",
            columns=cols,
            column_indexes=indexes,
            uniqueness_ds=stats["uniqueness_ds"],
            uniqueness_infa=stats["uniqueness_infa"],
            overlap=stats["overlap"],
        )

    if settings.key_mode == "none":
        return KeyResult(
            source="None",
            fallback_reason="KEY_MODE is none",
        )

    # Auto detection
    return _auto_detect(con, ds, infa, display_headers, column_diffs, settings)


def _key_stats(
    con: duckdb.DuckDBPyConnection,
    ds: FileInfo,
    infa: FileInfo,
    indexes: list[int],
) -> dict:
    key_cols = ", ".join(f"c{i + 1}" for i in indexes)
    ds_distinct_key = int(
        con.execute(
            f"SELECT count(*) FROM (SELECT DISTINCT {key_cols} FROM {ds.table_name})"
        ).fetchone()[0]
    )
    infa_distinct_key = int(
        con.execute(
            f"SELECT count(*) FROM (SELECT DISTINCT {key_cols} FROM {infa.table_name})"
        ).fetchone()[0]
    )
    both = int(
        con.execute(
            f"""
            SELECT count(*) FROM
            (SELECT DISTINCT {key_cols} FROM {ds.table_name}) d
            INNER JOIN
            (SELECT DISTINCT {key_cols} FROM {infa.table_name}) i
            USING ({key_cols})
            """
        ).fetchone()[0]
    )
    larger = max(ds_distinct_key, infa_distinct_key)
    overlap = (both / larger) if larger else 0.0
    return {
        "uniqueness_ds": f"{ds_distinct_key} / {ds.distinct_rows}",
        "uniqueness_infa": f"{infa_distinct_key} / {infa.distinct_rows}",
        "overlap": overlap,
        "ds_distinct_key": ds_distinct_key,
        "infa_distinct_key": infa_distinct_key,
        "unique_ds": ds_distinct_key == ds.distinct_rows,
        "unique_infa": infa_distinct_key == infa.distinct_rows,
    }


def _auto_detect(
    con: duckdb.DuckDBPyConnection,
    ds: FileInfo,
    infa: FileInfo,
    display_headers: list[str],
    column_diffs: list[ColumnDiff],
    settings: Settings,
) -> KeyResult:
    min_overlap = settings.key_min_overlap
    candidates = [
        cd
        for cd in column_diffs
        if cd.overlap >= min_overlap
    ]
    if not candidates:
        return KeyResult(
            source="None",
            fallback_reason="No column with sufficient overlap",
        )

    # Single column
    singles = [cd for cd in candidates if cd.unique_ds and cd.unique_infa]
    if singles:
        def rank(cd: ColumnDiff) -> tuple:
            hint = 0 if _header_matches_hint(cd.name, settings.key_name_hints) else 1
            return (hint, -cd.overlap, cd.position)

        best = sorted(singles, key=rank)[0]
        idx = best.position - 1
        return KeyResult(
            source="Auto-detected",
            columns=[best.name],
            column_indexes=[idx],
            uniqueness_ds=f"{best.ds_distinct} / {ds.distinct_rows}",
            uniqueness_infa=f"{best.infa_distinct} / {infa.distinct_rows}",
            overlap=best.overlap,
        )

    # Combinations
    top = sorted(candidates, key=lambda c: -max(c.ds_distinct, c.infa_distinct))[
        : settings.key_candidate_limit
    ]
    tried = 0
    max_cols = settings.key_max_columns
    for k in range(2, max_cols + 1):
        for combo in itertools.combinations(top, k):
            if tried >= 20:
                break
            tried += 1
            indexes = [c.position - 1 for c in combo]
            # approx screen
            if not _approx_unique_enough(con, ds, infa, indexes):
                continue
            stats = _key_stats(con, ds, infa, indexes)
            if not (stats["unique_ds"] and stats["unique_infa"]):
                continue
            if stats["overlap"] < min_overlap:
                continue
            cols = [display_headers[i] for i in indexes]
            return KeyResult(
                source="Auto-detected",
                columns=cols,
                column_indexes=indexes,
                uniqueness_ds=stats["uniqueness_ds"],
                uniqueness_infa=stats["uniqueness_infa"],
                overlap=stats["overlap"],
            )
        if tried >= 20:
            break

    return KeyResult(
        source="None",
        fallback_reason="No unique key combination found",
    )


def _approx_unique_enough(
    con: duckdb.DuckDBPyConnection,
    ds: FileInfo,
    infa: FileInfo,
    indexes: list[int],
) -> bool:
    expr = " || '|' || ".join(f"c{i + 1}" for i in indexes)
    # Skip if approx distinct clearly below distinct row count
    for table, distinct_rows in (
        (ds.table_name, ds.distinct_rows),
        (infa.table_name, infa.distinct_rows),
    ):
        approx = con.execute(
            f"SELECT approx_count_distinct({expr}) FROM {table}"
        ).fetchone()[0]
        # "clearly below" — allow some approx error; require at least 90%
        if approx is not None and distinct_rows > 0 and approx < distinct_rows * 0.9:
            return False
    return True
