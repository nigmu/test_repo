"""Orchestrate pairing, per-pair steps, errors, OOM retry, cleanup (sections 3, 16–17)."""

from __future__ import annotations

import shutil
import time
import traceback
from pathlib import Path

import psutil

from . import (
    columns,
    csvformat,
    csvout,
    db,
    differences,
    encoding,
    excel,
    files,
    keys,
    rowcompare,
    selftest,
)
from .model import FileInfo, PairResult, StepTiming
from .runlog import RunLog
from .settings import Settings


def _empty_temp(temp_folder: Path) -> None:
    if not temp_folder.exists():
        temp_folder.mkdir(parents=True, exist_ok=True)
        return
    for p in temp_folder.iterdir():
        try:
            if p.is_file():
                p.unlink()
            elif p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
        except OSError:
            pass


def _disk_probe(folder: Path) -> Path:
    """Return a directory that exists, for a free-space check.

    On Linux a relative path that does not exist yet has an empty anchor.
    disk_usage rejects that empty path, so walk up to a real directory.
    """
    current = Path(folder)
    while not current.exists():
        parent = current.parent
        if parent == current:
            return Path.cwd()
        current = parent
    return current


def _disk_ok(settings: Settings, ds_path: Path, infa_path: Path) -> tuple[bool, str]:
    needed = settings.disk_free_factor * (ds_path.stat().st_size + infa_path.stat().st_size)
    usage = shutil.disk_usage(_disk_probe(settings.output_folder))
    if usage.free < needed:
        return False, (
            f"Need {needed / (1024**3):.2f} GB free "
            f"(factor {settings.disk_free_factor} × file sizes), "
            f"have {usage.free / (1024**3):.2f} GB"
        )
    return True, ""


def _timed(timings: list[StepTiming], name: str, start: float) -> None:
    timings.append(StepTiming(name=name, seconds=time.perf_counter() - start))


def process_pair(
    pair: files.FilePair,
    settings: Settings,
    runlog: RunLog,
    *,
    threads_override: int | None = None,
) -> PairResult:
    t0 = time.perf_counter()
    timings: list[StepTiming] = []
    ds = FileInfo(side="ds", path=pair.ds_path, name=pair.ds_path.name)
    infa = FileInfo(side="infa", path=pair.infa_path, name=pair.infa_path.name)
    result = PairResult(prefix=pair.prefix, ds=ds, infa=infa)
    con = None
    db_path = settings.temp_folder / f"{pair.prefix}.duckdb"
    result.db_path = db_path
    peak_rss = psutil.Process().memory_info().rss

    def note_rss() -> None:
        nonlocal peak_rss
        peak_rss = max(peak_rss, psutil.Process().memory_info().rss)

    try:
        ok, msg = _disk_ok(settings, pair.ds_path, pair.infa_path)
        if not ok:
            result.overall = "ERROR"
            result.error_message = f"Not enough disk space: {msg}"
            _write_error_workbook(result, settings)
            return result

        # Encoding
        t = time.perf_counter()
        ds.encoding = encoding.prepare_encoding(
            pair.ds_path, settings.temp_folder, settings, "ds", pair.prefix
        )
        infa.encoding = encoding.prepare_encoding(
            pair.infa_path, settings.temp_folder, settings, "infa", pair.prefix
        )
        if ds.encoding.is_temp:
            result.temp_paths.append(ds.encoding.path_for_reader)
        if infa.encoding.is_temp:
            result.temp_paths.append(infa.encoding.path_for_reader)
        if ds.encoding.warning:
            result.warnings.append(ds.encoding.warning)
        if infa.encoding.warning:
            result.warnings.append(infa.encoding.warning)
        _timed(timings, "encoding check", t)
        note_rss()

        # Open DB early for sniff
        con, mem, threads = db.open_pair_db(
            db_path, settings.temp_folder, settings, threads_override=threads_override
        )
        result.memory_limit = mem
        result.threads = threads
        result.temp_paths.append(db_path)

        # Format
        t = time.perf_counter()
        ds.csv_format = csvformat.sniff_format(
            ds.encoding.path_for_reader, settings, pair.prefix, "ds", con
        )
        infa.csv_format = csvformat.sniff_format(
            infa.encoding.path_for_reader, settings, pair.prefix, "infa", con
        )
        _timed(timings, "format detection", t)

        # Headers
        ds.headers_raw = csvformat.read_headers(ds.encoding.path_for_reader, ds.csv_format)
        infa.headers_raw = csvformat.read_headers(
            infa.encoding.path_for_reader, infa.csv_format
        )
        ds.column_count = len(ds.headers_raw)
        infa.column_count = len(infa.headers_raw)
        header_cmp = csvformat.compare_headers(ds.headers_raw, infa.headers_raw)
        result.header_compare = header_cmp
        result.columns_match = header_cmp.match
        ds.headers_display = header_cmp.display_headers
        infa.headers_display = (
            csvformat.make_display_headers(infa.headers_raw)
            if not header_cmp.match
            else header_cmp.display_headers
        )

        if not header_cmp.match:
            t = time.perf_counter()
            db.count_only(
                con, ds, max(ds.column_count, 1), settings, "ds_count", "ds_rejects", "ds_scans"
            )
            db.count_only(
                con,
                infa,
                max(infa.column_count, 1),
                settings,
                "infa_count",
                "infa_rejects",
                "infa_scans",
            )
            _timed(timings, "count rows", t)
            result.overall = "COLUMNS DIFFER – comparison stopped"
            result.timings = timings
            result.total_seconds = time.perf_counter() - t0
            out = excel.write_workbook(con, result, settings, settings.output_folder)
            result.outputs.append(out)
            return result

        # Load
        t = time.perf_counter()
        n = ds.column_count
        try:
            db.load_file(con, ds, n, settings, "ds", "ds_rejects", "ds_scans")
            db.load_file(con, infa, n, settings, "infa", "infa_rejects", "infa_scans")
        except db.MaxLineSizeError as e:
            result.overall = "ERROR"
            result.error_message = str(e)
            result.timings = timings
            result.total_seconds = time.perf_counter() - t0
            out = excel.write_workbook(con, result, settings, settings.output_folder)
            result.outputs.append(out)
            return result
        _timed(timings, "load", t)
        note_rss()

        if ds.rejected_lines or infa.rejected_lines:
            result.warnings.append("Rejected lines present")

        if db.rejects_over_limit(ds, settings) or db.rejects_over_limit(infa, settings):
            result.overall = "Too many malformed lines"
            result.error_message = "Too many malformed lines"
            # Still write rejected CSV + summary
            rej = csvout.write_rejected_csv_to(
                con, settings.output_folder, pair.prefix, ds, infa
            )
            if rej:
                result.outputs.append(rej)
            result.timings = timings
            result.total_seconds = time.perf_counter() - t0
            out = excel.write_workbook(con, result, settings, settings.output_folder)
            result.outputs.append(out)
            return result

        # Row compare
        t = time.perf_counter()
        rc = rowcompare.compare_rows(con, ds, infa)
        result.row_compare = rc
        _timed(timings, "row comparison", t)
        note_rss()

        # Column analysis
        t = time.perf_counter()
        result.column_diffs = columns.analyze_columns(
            con, ds, infa, header_cmp.display_headers, settings, rc.cmp_table
        )
        _timed(timings, "column analysis", t)
        note_rss()

        # Key
        t = time.perf_counter()
        key = keys.select_key(
            con,
            pair.prefix,
            ds,
            infa,
            header_cmp.display_headers,
            result.column_diffs,
            settings,
        )
        result.key = key
        if key.invalid:
            result.overall = "ERROR"
            result.error_message = f"Invalid key in configs.py: {key.invalid_reason}"
            result.timings = timings
            result.total_seconds = time.perf_counter() - t0
            out = excel.write_workbook(con, result, settings, settings.output_folder)
            result.outputs.append(out)
            return result

        diff_t, unpaired_t, mismatch_t = differences.build_differences(
            con, ds, infa, header_cmp.display_headers, key, rc
        )
        result.differences_table = diff_t
        result.unpaired_table = unpaired_t
        result.mismatches_table = mismatch_t
        if diff_t and key.columns:
            columns.update_paired_differing(
                con, result.column_diffs, diff_t, key.column_indexes
            )
        _timed(timings, "key selection and pairing", t)
        note_rss()

        # Outputs
        t = time.perf_counter()
        if diff_t and key.columns:
            o = csvout.write_differences_csv_to(
                con, settings.output_folder, pair.prefix, diff_t, key.columns
            )
            if o:
                result.outputs.append(o)
        if unpaired_t and key.columns:
            o = csvout.write_unpaired_csv_to(
                con,
                settings.output_folder,
                pair.prefix,
                unpaired_t,
                header_cmp.display_headers,
            )
            if o:
                result.outputs.append(o)
        if mismatch_t and not key.columns:
            o = csvout.write_mismatches_csv_to(
                con,
                settings.output_folder,
                pair.prefix,
                mismatch_t,
                header_cmp.display_headers,
            )
            if o:
                result.outputs.append(o)
        if ds.rejected_lines or infa.rejected_lines:
            o = csvout.write_rejected_csv_to(
                con, settings.output_folder, pair.prefix, ds, infa
            )
            if o:
                result.outputs.append(o)

        if (
            rc.matched_rows == ds.row_count == infa.row_count
            and rc.only_ds == 0
            and rc.only_infa == 0
            and rc.extra_ds == 0
            and rc.extra_infa == 0
        ):
            result.overall = "MATCH"
        else:
            result.overall = "MISMATCH"

        result.timings = timings
        result.total_seconds = time.perf_counter() - t0
        out = excel.write_workbook(con, result, settings, settings.output_folder)
        result.outputs.append(out)
        _timed(timings, "output writing", t)
        note_rss()

        result.peak_rss_mb = peak_rss / (1024 * 1024)
        result.total_seconds = time.perf_counter() - t0
        return result

    finally:
        if con is not None:
            try:
                con.close()
            except Exception:
                pass
        result.peak_rss_mb = max(result.peak_rss_mb, peak_rss / (1024 * 1024))


def _write_error_workbook(result: PairResult, settings: Settings) -> None:
    try:
        out = excel.write_workbook(None, result, settings, settings.output_folder)
        result.outputs.append(out)
    except Exception:
        pass


def _is_oom(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return "out of memory" in msg or "memory limit" in msg or "oom" in msg


def process_pair_with_retry(
    pair: files.FilePair, settings: Settings, runlog: RunLog
) -> PairResult:
    keep_temp = False
    try:
        try:
            result = process_pair(pair, settings, runlog)
        except Exception as e:
            if _is_oom(e):
                runlog.log(f"{pair.prefix}: OOM, retrying with threads=1")
                result = process_pair(pair, settings, runlog, threads_override=1)
            else:
                raise
        return result
    except Exception as e:
        keep_temp = settings.keep_temp_on_error
        runlog.log(f"{pair.prefix}: ERROR {e}")
        runlog.log(traceback.format_exc(), also_print=False)
        ds = FileInfo(side="ds", path=pair.ds_path, name=pair.ds_path.name)
        infa = FileInfo(side="infa", path=pair.infa_path, name=pair.infa_path.name)
        result = PairResult(
            prefix=pair.prefix,
            ds=ds,
            infa=infa,
            overall="ERROR",
            error_message=str(e),
        )
        _write_error_workbook(result, settings)
        return result
    finally:
        if not keep_temp:
            _cleanup_pair_temp(pair.prefix, settings)


def _cleanup_pair_temp(prefix: str, settings: Settings) -> None:
    temp = settings.temp_folder
    if not temp.exists():
        return
    patterns = [
        f"{prefix}.duckdb",
        f"{prefix}.duckdb.wal",
        f"{prefix}_ds_utf8.csv",
        f"{prefix}_infa_utf8.csv",
    ]
    for name in patterns:
        p = temp / name
        try:
            if p.exists():
                p.unlink()
        except OSError:
            pass
    # DuckDB spill files
    for p in temp.glob(f"*{prefix}*"):
        try:
            if p.is_file():
                p.unlink()
        except OSError:
            pass


def run(settings: Settings | None = None) -> int:
    if settings is None:
        settings = Settings.from_configs()

    settings.output_folder.mkdir(parents=True, exist_ok=True)
    settings.temp_folder.mkdir(parents=True, exist_ok=True)

    runlog = RunLog(settings.output_folder)
    runlog.log("Cleaning temporary folder")
    _empty_temp(settings.temp_folder)

    runlog.log("Running self-test")
    try:
        selftest.run_selftest(settings, settings.temp_folder)
    except Exception as e:
        runlog.log(f"SELF-TEST FAILED: {e}")
        runlog.close()
        return 2
    runlog.log("Self-test passed")

    discovery = files.discover_pairs(settings)
    for ign in discovery.ignored:
        runlog.log(f"Ignored file: {ign}")
    for up in discovery.unpaired:
        runlog.log(f"No partner: {up}")

    if not discovery.pairs:
        runlog.log("No pairs found")
        runlog.close()
        return 0

    for pair in discovery.pairs:
        runlog.log(f"=== Pair {pair.prefix} start ===")
        result = process_pair_with_retry(pair, settings, runlog)
        runlog.log(
            f"Pair {pair.prefix}: {result.overall} "
            f"(rows ds={result.ds.row_count} infa={result.infa.row_count}, "
            f"{result.total_seconds:.1f}s, peak RSS {result.peak_rss_mb:.0f} MB)"
        )
        for o in result.outputs:
            renamed = " (renamed, target locked)" if o.renamed else ""
            runlog.log(f"  wrote {o.path.name}{renamed}")
        runlog.flush()

    runlog.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    return run()
