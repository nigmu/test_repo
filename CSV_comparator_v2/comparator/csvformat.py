"""CSV format sniffing and header comparison (DESIGN.md sections 5–6)."""

from __future__ import annotations

import csv
from pathlib import Path

import duckdb

from .model import CsvFormat, HeaderCompare
from .settings import Settings


def _normalize_header(name: str) -> str:
    return name.strip().casefold()


def make_display_headers(raw: list[str]) -> list[str]:
    """Unique display names; duplicates get ' (position)' suffix (1-based)."""
    norm_counts: dict[str, int] = {}
    for h in raw:
        key = _normalize_header(h)
        norm_counts[key] = norm_counts.get(key, 0) + 1

    display: list[str] = []
    for i, h in enumerate(raw):
        key = _normalize_header(h)
        if norm_counts[key] > 1:
            # Position suffix; show trimmed base name plus 1-based position
            display.append(f"{h.strip()} ({i + 1})")
        else:
            # As written in the file (DataStage headers used for outputs)
            display.append(h)
    return display


def sniff_format(
    path: Path,
    settings: Settings,
    prefix: str,
    side: str,
    con: duckdb.DuckDBPyConnection | None = None,
) -> CsvFormat:
    """Detect or override CSV format for one file."""
    overrides = (settings.format_overrides or {}).get(prefix, {})
    side_override = overrides.get(side) if isinstance(overrides, dict) else None
    if side_override:
        delim = side_override.get("delim", ",")
        quote = side_override.get("quote", '"')
        escape = side_override.get("escape", quote)
        return CsvFormat(
            delim=delim,
            quote=quote,
            escape=escape,
            source="configs.py",
        )

    own = con is None
    if own:
        con = duckdb.connect(":memory:")
    try:
        # sniff_csv returns a table; sample size via sample_size parameter
        rel = con.sql(
            f"SELECT * FROM sniff_csv(?, sample_size={int(settings.sniff_sample_rows)})",
            params=[str(path)],
        )
        row = rel.fetchone()
        cols = [d[0] for d in rel.description]
        data = dict(zip(cols, row)) if row else {}

        delim = data.get("Delimiter") or ","
        quote_raw = data.get("Quote")
        escape_raw = data.get("Escape")

        # DuckDB returns the string "(empty)" when quote/escape are missing
        sniff_quote_empty = quote_raw is None or str(quote_raw) in ("", "(empty)")
        sniff_escape_empty = escape_raw is None or str(escape_raw) in ("", "(empty)")

        if sniff_quote_empty:
            quote = '"'
        else:
            quote = str(quote_raw)

        if sniff_escape_empty:
            # escape='' makes read_csv fail; use doubled-quote style
            escape = quote
        else:
            escape = str(escape_raw)

        note = ""
        if sniff_quote_empty or sniff_escape_empty:
            parts = []
            if sniff_quote_empty:
                parts.append("quote was empty from sniffer")
            if sniff_escape_empty:
                parts.append("escape was empty from sniffer")
            note = (
                "Using quote '\"' and escape '\"' (doubled-quote style); "
                + "; ".join(parts)
            )

        return CsvFormat(
            delim=str(delim),
            quote=quote,
            escape=escape,
            source="detected",
            sniff_quote_empty=sniff_quote_empty,
            sniff_escape_empty=sniff_escape_empty,
            note=note,
        )
    finally:
        if own and con is not None:
            con.close()


def read_headers(path: Path, fmt: CsvFormat) -> list[str]:
    """Read the first CSV record as headers using the csv module."""
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f, delimiter=fmt.delim, quotechar=fmt.quote)
        try:
            row = next(reader)
        except StopIteration:
            return []
    if row and row[0].startswith("\ufeff"):
        row[0] = row[0].lstrip("\ufeff")
    return row


def compare_headers(ds_headers: list[str], infa_headers: list[str]) -> HeaderCompare:
    n = max(len(ds_headers), len(infa_headers))
    positions: list[dict] = []
    match = len(ds_headers) == len(infa_headers)
    for i in range(n):
        ds_h = ds_headers[i] if i < len(ds_headers) else ""
        infa_h = infa_headers[i] if i < len(infa_headers) else ""
        if i >= len(ds_headers):
            status = "Extra in Informatica"
            match = False
        elif i >= len(infa_headers):
            status = "Extra in DataStage"
            match = False
        elif _normalize_header(ds_h) == _normalize_header(infa_h):
            status = "Match"
        else:
            status = "Different"
            match = False
        positions.append(
            {
                "position": i + 1,
                "ds": ds_h,
                "infa": infa_h,
                "status": status,
            }
        )

    display = make_display_headers(ds_headers) if match else make_display_headers(
        ds_headers if ds_headers else infa_headers
    )
    return HeaderCompare(
        match=match,
        ds_headers=ds_headers,
        infa_headers=infa_headers,
        display_headers=display,
        positions=positions,
    )
