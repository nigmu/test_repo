"""Run 10M-row pairs and record per-step time and peak RSS."""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from comparator.files import FilePair  # noqa: E402
from comparator.runlog import RunLog  # noqa: E402
from comparator.runner import process_pair  # noqa: E402
from comparator.settings import Settings  # noqa: E402
from tests.make_test_data import DS, INFA, write_large_pair  # noqa: E402


class RssSampler:
    def __init__(self, interval: float = 0.5) -> None:
        self.interval = interval
        self.peak = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self.peak = psutil.Process().memory_info().rss
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        proc = psutil.Process()
        while not self._stop.wait(self.interval):
            self.peak = max(self.peak, proc.memory_info().rss)

    def stop(self) -> float:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        return self.peak / (1024 * 1024)


def run_one(prefix: str, all_mismatch: bool, rows: int, cols: int) -> dict:
    print(f"Generating {prefix} ({rows} x {cols}, mismatch={all_mismatch})...")
    t_gen = time.perf_counter()
    write_large_pair(rows, cols, all_mismatch=all_mismatch, prefix=prefix)
    gen_s = time.perf_counter() - t_gen
    print(f"  generation: {gen_s:.1f}s")

    out = ROOT / "OUTPUT" / "large_run"
    temp = out / "_tmp"
    out.mkdir(parents=True, exist_ok=True)
    temp.mkdir(parents=True, exist_ok=True)

    settings = Settings(
        ds_folder=DS,
        infa_folder=INFA,
        output_folder=out,
        temp_folder=temp,
        excel_sample_rows=1000,
        key_overrides={prefix: ["ID"]},
        # Default factor 4 needs ~tens of GB free; large-run harness uses 2.
        disk_free_factor=2.0,
    )
    pair = FilePair(
        prefix=prefix,
        ds_path=DS / f"{prefix}_ds.csv",
        infa_path=INFA / f"{prefix}_infa.csv",
    )
    sampler = RssSampler(0.25)
    sampler.start()
    t0 = time.perf_counter()
    result = process_pair(pair, settings, RunLog(out))
    elapsed = time.perf_counter() - t0
    peak_mb = sampler.stop()
    peak_mb = max(peak_mb, result.peak_rss_mb)

    report = {
        "prefix": prefix,
        "all_mismatch": all_mismatch,
        "rows": rows,
        "cols": cols,
        "overall": result.overall,
        "generation_s": gen_s,
        "total_s": elapsed,
        "peak_rss_mb": peak_mb,
        "memory_limit": result.memory_limit,
        "threads": result.threads,
        "matched_rows": result.row_compare.matched_rows if result.row_compare else None,
        "differing_cells": result.key.differing_cells if result.key else None,
        "timings": [{"name": t.name, "seconds": t.seconds} for t in result.timings],
        "error": result.error_message,
    }
    print(json.dumps(report, indent=2))
    # Free DuckDB between pairs (process_pair does not clean temp)
    try:
        if result.db_path and result.db_path.exists():
            # connection already closed by process_pair finally
            result.db_path.unlink(missing_ok=True)
        wal = Path(str(result.db_path) + ".wal") if result.db_path else None
        if wal and wal.exists():
            wal.unlink(missing_ok=True)
    except OSError as e:
        print(f"temp cleanup warning: {e}")
    return report


def main() -> int:
    rows = 10_000_000
    cols = 30
    reports = []
    for prefix, mm in (("large", False), ("large_mm", True)):
        reports.append(run_one(prefix, mm, rows, cols))
    out_path = ROOT / "OUTPUT" / "large_run" / "large_run_report.json"
    out_path.write_text(json.dumps(reports, indent=2), encoding="utf-8")
    print(f"Wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
