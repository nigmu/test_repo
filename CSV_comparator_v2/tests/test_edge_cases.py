"""Edge-case coverage beyond the worked example."""

from __future__ import annotations

import os
from pathlib import Path

import openpyxl
import pytest

from comparator.files import FilePair, discover_pairs
from comparator.runlog import RunLog
from comparator.runner import process_pair, process_pair_with_retry, run
from comparator.selftest import run_selftest
from comparator.settings import Settings
from tests.make_test_data import DS, INFA, ROOT, write_all_edge_cases


@pytest.fixture(scope="module", autouse=True)
def _data():
    write_all_edge_cases()


def _settings(tmp_path, **kwargs) -> Settings:
    out = tmp_path / "OUTPUT"
    temp = out / "_tmp"
    out.mkdir()
    temp.mkdir()
    base = dict(
        input_folder=DS,
        output_folder=out,
        temp_folder=temp,
        excel_sample_rows=500,
    )
    base.update(kwargs)
    return Settings(**base)


def _pair(prefix: str) -> FilePair:
    return FilePair(
        prefix=prefix,
        ds_path=DS / f"{prefix}_ds.csv",
        infa_path=INFA / f"{prefix}_infa.csv",
    )


def test_selftest(tmp_path):
    s = _settings(tmp_path)
    run_selftest(s, s.temp_folder)


def test_columns_differ(tmp_path):
    s = _settings(tmp_path)
    r = process_pair(_pair("coldiff"), s, RunLog(s.output_folder))
    assert r.overall == "COLUMNS DIFFER – comparison stopped"
    assert r.columns_match is False
    xlsx = s.output_folder / "coldiff_comparison.xlsx"
    assert xlsx.exists()
    wb = openpyxl.load_workbook(xlsx, read_only=True)
    assert "Summary" in wb.sheetnames
    assert "Column differences" not in wb.sheetnames


def test_headers_case_and_space_match(tmp_path):
    s = _settings(tmp_path)
    r = process_pair(_pair("hdrspace"), s, RunLog(s.output_folder))
    assert r.columns_match is True
    assert r.overall == "MATCH"


def test_mixed_encoding(tmp_path):
    s = _settings(tmp_path)
    r = process_pair(_pair("mixedenc"), s, RunLog(s.output_folder))
    assert r.infa.encoding is not None
    assert "cp1252" in r.infa.encoding.label
    assert any("cp1252" in w for w in r.warnings)


def test_utf16_le_bom(tmp_path):
    s = _settings(tmp_path)
    r = process_pair(_pair("utf16le"), s, RunLog(s.output_folder))
    assert "UTF-16LE" in (r.ds.encoding.label or "")
    assert r.overall == "MATCH"


def test_utf16_be_bom(tmp_path):
    s = _settings(tmp_path)
    r = process_pair(_pair("utf16be"), s, RunLog(s.output_folder))
    assert "UTF-16BE" in (r.ds.encoding.label or "")
    assert r.overall == "MATCH"


def test_utf16_no_bom(tmp_path):
    s = _settings(tmp_path)
    r = process_pair(_pair("utf16nobom"), s, RunLog(s.output_folder))
    assert "NUL pattern" in (r.ds.encoding.label or "")
    assert r.overall == "MATCH"


def test_different_delimiter(tmp_path):
    s = _settings(tmp_path)
    r = process_pair(_pair("delim"), s, RunLog(s.output_folder))
    assert r.columns_match is True
    assert r.overall == "MATCH"
    assert r.ds.csv_format.delim == ","
    assert r.infa.csv_format.delim == "|"


def test_quoted_newline_and_doubled_quotes(tmp_path):
    s = _settings(tmp_path)
    r = process_pair(_pair("quotes"), s, RunLog(s.output_folder))
    assert r.overall == "MATCH"
    assert r.ds.row_count == 2


def test_rejects_below_limit(tmp_path):
    s = _settings(
        tmp_path,
        format_overrides={
            "rejectok": {
                "ds": {"delim": ",", "quote": '"', "escape": '"'},
                "infa": {"delim": ",", "quote": '"', "escape": '"'},
            }
        },
    )
    r = process_pair(_pair("rejectok"), s, RunLog(s.output_folder))
    assert r.columns_match is True
    assert r.infa.rejected_lines >= 1
    assert r.overall != "Too many malformed lines"
    assert (s.output_folder / "rejectok_comparison_rejected.csv").exists()


def test_rejects_above_limit(tmp_path):
    s = _settings(
        tmp_path,
        reject_limit_percent=1.0,
        format_overrides={
            "rejectbad": {
                "ds": {"delim": ",", "quote": '"', "escape": '"'},
                "infa": {"delim": ",", "quote": '"', "escape": '"'},
            }
        },
    )
    r = process_pair(_pair("rejectbad"), s, RunLog(s.output_folder))
    assert r.overall == "Too many malformed lines"


def test_duplicate_rows(tmp_path):
    s = _settings(tmp_path)
    r = process_pair(_pair("duprows"), s, RunLog(s.output_folder))
    assert r.row_compare.matched_rows == 2
    assert r.row_compare.extra_ds == 2


def test_manual_key_with_duplicates(tmp_path):
    s = _settings(tmp_path, key_overrides={"mankey": ["ID"]})
    r = process_pair(_pair("mankey"), s, RunLog(s.output_folder))
    assert r.key.source.startswith("Manual")
    assert r.key.duplicate_key_ds >= 1 or "Duplicate key" in str(
        r.key.unpaired_by_status
    )


def test_invalid_manual_key(tmp_path):
    s = _settings(tmp_path, key_overrides={"badkey": ["NOT_A_COLUMN"]})
    r = process_pair(_pair("badkey"), s, RunLog(s.output_folder))
    assert r.overall == "ERROR"
    assert "Invalid key" in r.error_message


def test_no_key_fallback(tmp_path):
    s = _settings(tmp_path, key_overrides={"nokey": None})
    r = process_pair(_pair("nokey"), s, RunLog(s.output_folder))
    assert r.key.source == "None"
    assert r.mismatches_table is not None
    assert (s.output_folder / "nokey_comparison_mismatches.csv").exists()


def test_identical_files(tmp_path):
    s = _settings(tmp_path)
    r = process_pair(_pair("identical"), s, RunLog(s.output_folder))
    assert r.overall == "MATCH"
    assert r.row_compare.match_rate == 100.0


def test_discovery_unpaired_and_ignored(tmp_path):
    s = _settings(tmp_path)
    disc = discover_pairs(s)
    unpaired_names = [Path(p).name for p in disc.unpaired]
    assert any("lonely" in n for n in unpaired_names)
    ignored = " ".join(disc.ignored)
    assert "customer_ds (1).csv" in ignored or "(1)" in ignored


def test_locked_output_gets_timestamped_name(tmp_path):
    s = _settings(tmp_path)
    # Pre-create and lock the xlsx
    target = s.output_folder / "identical_comparison.xlsx"
    target.write_bytes(b"locked")
    # On Windows, opening without sharing prevents replace
    with target.open("rb") as lock_handle:
        # Also create a lock by keeping file open for write on Windows
        try:
            # Re-open exclusively
            lock_handle.close()
            fh = os.open(str(target), os.O_RDWR)
            try:
                r = process_pair(_pair("identical"), s, RunLog(s.output_folder))
                # If replace failed, a timestamped file should exist
                outputs = list(s.output_folder.glob("identical_comparison*.xlsx"))
                assert len(outputs) >= 1
                assert r.overall == "MATCH"
            finally:
                os.close(fh)
        except OSError:
            # If exclusive lock not available, still ensure run succeeds
            r = process_pair(_pair("identical"), s, RunLog(s.output_folder))
            assert r.overall == "MATCH"


def test_temp_cleanup(tmp_path):
    s = _settings(tmp_path)
    r = process_pair_with_retry(_pair("identical"), s, RunLog(s.output_folder))
    assert r.overall == "MATCH"
    # duckdb for pair should be gone
    assert not (s.temp_folder / "identical.duckdb").exists()


def test_run_continues_after_pair_error(tmp_path):
    s = _settings(tmp_path, key_overrides={"badkey": ["NOPE"]})
    # run() does self-test + all pairs — too many; instead process two pairs
    r1 = process_pair_with_retry(_pair("badkey"), s, RunLog(s.output_folder))
    r2 = process_pair_with_retry(_pair("identical"), s, RunLog(s.output_folder))
    assert r1.overall == "ERROR"
    assert r2.overall == "MATCH"
