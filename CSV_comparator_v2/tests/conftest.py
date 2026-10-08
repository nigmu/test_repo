"""Shared fixtures for comparator tests."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.make_test_data import write_all_edge_cases  # noqa: E402
from comparator.settings import Settings  # noqa: E402


@pytest.fixture(scope="session")
def edge_data():
    write_all_edge_cases()
    return ROOT / "INPUT"


@pytest.fixture
def tmp_settings(tmp_path, edge_data) -> Settings:
    out = tmp_path / "OUTPUT"
    temp = out / "_tmp"
    out.mkdir()
    temp.mkdir()
    return Settings(
        input_folder=ROOT / "INPUT",
        output_folder=out,
        temp_folder=temp,
        excel_sample_rows=1000,
        fetch_batch_rows=100,
    )
