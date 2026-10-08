"""Check whether CSV columns are written with quotes.

CSV files go in input_for_quote_check. The terminal lists columns written
with quotes and columns written without them. The full detail for each
file is written to output_for_quote_check.
"""

from .report import render
from .scan import scan_path, scan_text

__all__ = ["render", "scan_path", "scan_text"]
