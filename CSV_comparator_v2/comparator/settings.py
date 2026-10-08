"""Settings dataclass built from configs.py or ad-hoc for tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Settings:
    ds_folder: Path
    infa_folder: Path
    output_folder: Path
    temp_folder: Path
    ds_suffix: str = "_ds.csv"
    infa_suffix: str = "_infa.csv"
    memory_limit_max_gb: float = 2.0
    memory_limit_min_gb: float = 1.0
    memory_share_of_available: float = 0.5
    threads: int | None = None
    read_chunk_mb: int = 16
    sniff_sample_rows: int = 20480
    format_overrides: dict[str, Any] = field(default_factory=dict)
    max_line_size_bytes: int = 2097152
    reject_limit_percent: float = 1.0
    key_mode: str = "auto"
    key_overrides: dict[str, Any] = field(default_factory=dict)
    key_min_overlap: float = 0.80
    key_max_columns: int = 3
    key_candidate_limit: int = 5
    key_name_hints: list[str] = field(
        default_factory=lambda: ["ID", "KEY", "NO", "NUM", "CODE"]
    )
    example_values: int = 3
    excel_sample_rows: int = 100000
    excel_quote_values: bool = True
    fetch_batch_rows: int = 10000
    disk_free_factor: float = 4.0
    keep_temp_on_error: bool = False

    @classmethod
    def from_configs(cls, configs_module: Any | None = None) -> Settings:
        if configs_module is None:
            import configs as configs_module  # type: ignore

        c = configs_module
        return cls(
            ds_folder=Path(c.DS_FOLDER),
            infa_folder=Path(c.INFA_FOLDER),
            output_folder=Path(c.OUTPUT_FOLDER),
            temp_folder=Path(c.TEMP_FOLDER),
            ds_suffix=c.DS_SUFFIX,
            infa_suffix=c.INFA_SUFFIX,
            memory_limit_max_gb=float(c.MEMORY_LIMIT_MAX_GB),
            memory_limit_min_gb=float(c.MEMORY_LIMIT_MIN_GB),
            memory_share_of_available=float(c.MEMORY_SHARE_OF_AVAILABLE),
            threads=c.THREADS,
            read_chunk_mb=int(c.READ_CHUNK_MB),
            sniff_sample_rows=int(c.SNIFF_SAMPLE_ROWS),
            format_overrides=dict(c.FORMAT_OVERRIDES or {}),
            max_line_size_bytes=int(c.MAX_LINE_SIZE_BYTES),
            reject_limit_percent=float(c.REJECT_LIMIT_PERCENT),
            key_mode=str(c.KEY_MODE),
            key_overrides=dict(c.KEY_OVERRIDES or {}),
            key_min_overlap=float(c.KEY_MIN_OVERLAP),
            key_max_columns=int(c.KEY_MAX_COLUMNS),
            key_candidate_limit=int(c.KEY_CANDIDATE_LIMIT),
            key_name_hints=list(c.KEY_NAME_HINTS),
            example_values=int(c.EXAMPLE_VALUES),
            excel_sample_rows=int(c.EXCEL_SAMPLE_ROWS),
            excel_quote_values=bool(c.EXCEL_QUOTE_VALUES),
            fetch_batch_rows=int(c.FETCH_BATCH_ROWS),
            disk_free_factor=float(c.DISK_FREE_FACTOR),
            keep_temp_on_error=bool(c.KEEP_TEMP_ON_ERROR),
        )
