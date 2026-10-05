"""Result dataclasses that feed the Summary and later stages."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class CsvFormat:
    delim: str
    quote: str
    escape: str
    source: str  # "detected" or "configs.py"
    sniff_quote_empty: bool = False
    sniff_escape_empty: bool = False
    note: str = ""


@dataclass
class EncodingInfo:
    label: str
    path_for_reader: Path
    is_temp: bool = False
    fallback_bytes: int = 0
    fallback_lines: list[int] = field(default_factory=list)
    warning: str = ""


@dataclass
class FileInfo:
    side: str  # "ds" or "infa"
    path: Path
    name: str
    encoding: EncodingInfo | None = None
    csv_format: CsvFormat | None = None
    headers_raw: list[str] = field(default_factory=list)
    headers_display: list[str] = field(default_factory=list)
    column_count: int = 0
    row_count: int = 0
    distinct_rows: int = 0
    rejected_lines: int = 0
    table_name: str = ""
    rejects_table: str = ""


@dataclass
class HeaderCompare:
    match: bool
    ds_headers: list[str]
    infa_headers: list[str]
    display_headers: list[str]
    positions: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class RowCompareResult:
    matched_rows: int = 0
    only_ds: int = 0
    only_infa: int = 0
    extra_ds: int = 0
    extra_infa: int = 0
    distinct_ds: int = 0
    distinct_infa: int = 0
    match_rate: float = 0.0
    cmp_table: str = "cmp"


@dataclass
class ColumnDiff:
    name: str
    position: int
    ds_type: str = ""
    infa_type: str = ""
    ds_integer: int = 0
    ds_decimal: int = 0
    ds_text: int = 0
    infa_integer: int = 0
    infa_decimal: int = 0
    infa_text: int = 0
    ds_distinct: int = 0
    infa_distinct: int = 0
    only_ds: int = 0
    only_infa: int = 0
    ds_examples: list[str] = field(default_factory=list)
    infa_examples: list[str] = field(default_factory=list)
    paired_differing: int | str = 0
    unique_ds: bool = False
    unique_infa: bool = False
    overlap: float = 0.0


@dataclass
class KeyResult:
    source: str  # "Manual (configs.py)", "Auto-detected", "None"
    columns: list[str] = field(default_factory=list)
    column_indexes: list[int] = field(default_factory=list)  # 0-based into display headers
    uniqueness_ds: str = ""
    uniqueness_infa: str = ""
    overlap: float | None = None
    paired_records: int = 0
    differing_cells: int = 0
    unpaired_by_status: dict[str, int] = field(default_factory=dict)
    fallback_reason: str = ""
    duplicate_key_ds: int = 0
    duplicate_key_infa: int = 0
    invalid: bool = False
    invalid_reason: str = ""


@dataclass
class OutputInfo:
    path: Path
    line_count: int = 0
    kind: str = ""  # xlsx / differences / unpaired / mismatches / rejected
    renamed: bool = False


@dataclass
class SheetInfo:
    name: str
    rows_shown: int = 0
    rows_total: int = 0
    truncated: bool = False
    note: str = ""


@dataclass
class StepTiming:
    name: str
    seconds: float


@dataclass
class PairResult:
    prefix: str
    ds: FileInfo
    infa: FileInfo
    overall: str = "MISMATCH"  # MATCH / MISMATCH / COLUMNS DIFFER – comparison stopped / ERROR / Too many...
    error_message: str = ""
    columns_match: bool | None = None
    header_compare: HeaderCompare | None = None
    row_compare: RowCompareResult | None = None
    column_diffs: list[ColumnDiff] = field(default_factory=list)
    key: KeyResult | None = None
    outputs: list[OutputInfo] = field(default_factory=list)
    sheets: list[SheetInfo] = field(default_factory=list)
    timings: list[StepTiming] = field(default_factory=list)
    total_seconds: float = 0.0
    warnings: list[str] = field(default_factory=list)
    excel_truncated_cells: int = 0
    memory_limit: str = ""
    threads: int = 0
    peak_rss_mb: float = 0.0
    # DuckDB table names used during the pair (for later stages)
    db_path: Path | None = None
    differences_table: str | None = None
    unpaired_table: str | None = None
    mismatches_table: str | None = None
    temp_paths: list[Path] = field(default_factory=list)
