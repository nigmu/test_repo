"""Slow 10M-row timing and memory tests."""

from __future__ import annotations

import time
from pathlib import Path

import psutil
import pytest

from comparator.files import FilePair
from comparator.runlog import RunLog
from comparator.runner import process_pair
from comparator.settings import Settings
from tests.make_test_data import DS, INFA, write_large_pair

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.slow
@pytest.mark.parametrize(
    "prefix,all_mismatch",
    [("large", False), ("large_mm", True)],
)
def test_large_pair(tmp_path, prefix, all_mismatch):
    rows = 10_000_000
    cols = 30
    write_large_pair(rows, cols, all_mismatch=all_mismatch, prefix=prefix)

    out = tmp_path / "OUTPUT"
    temp = out / "_tmp"
    out.mkdir()
    temp.mkdir()
    settings = Settings(
        input_folder=DS,
        output_folder=out,
        temp_folder=temp,
        excel_sample_rows=1000,
        key_overrides={prefix: ["ID"]},
    )
    pair = FilePair(
        prefix=prefix,
        ds_path=DS / f"{prefix}_ds.csv",
        infa_path=INFA / f"{prefix}_infa.csv",
    )
    proc = psutil.Process()
    rss_before = proc.memory_info().rss
    t0 = time.perf_counter()
    result = process_pair(pair, settings, RunLog(out))
    elapsed = time.perf_counter() - t0
    peak = result.peak_rss_mb

    assert result.overall in ("MATCH", "MISMATCH")
    assert result.ds.row_count == rows
    # Document timings
    print(f"\n=== {prefix} ===")
    print(f"total: {elapsed:.1f}s peak_rss: {peak:.0f} MB")
    for t in result.timings:
        print(f"  {t.name}: {t.seconds:.1f}s")
    print(f"memory_limit={result.memory_limit} threads={result.threads}")
    # Must not crash; RSS may exceed DuckDB cap
    assert peak > 0
