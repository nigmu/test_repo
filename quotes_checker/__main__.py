"""Command line entry for the quotes checker.

CSV files go in quotes_checker/input_for_quote_check.
A detail report for each file is written to quotes_checker/output_for_quote_check.
The terminal lists columns written with quotes and columns written without them.

    python -m quotes_checker
    python -m quotes_checker data.csv
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .report import render_terminal, write_reports
from .scan import Scan, scan_path


_DELIMS = {
    "comma": ",",
    ",": ",",
    "pipe": "|",
    "|": "|",
    "tab": "\t",
    "\\t": "\t",
    "semicolon": ";",
    ";": ";",
}

_QUOTES = {
    "double": '"',
    '"': '"',
    "single": "'",
    "'": "'",
}

_PACKAGE_DIR = Path(__file__).resolve().parent
INPUT_DIR = _PACKAGE_DIR / "input_for_quote_check"
OUTPUT_DIR = _PACKAGE_DIR / "output_for_quote_check"


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        delimiter = _parse_delim(args.delimiter) if args.delimiter else None
        quote = _parse_quote(args.quote) if args.quote else None
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if delimiter and quote and delimiter == quote:
        print("Delimiter and quote must be different characters.", file=sys.stderr)
        return 2
    if args.max_rows < 0:
        print("--max-rows must be 0 or greater.", file=sys.stderr)
        return 2

    INPUT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    if args.paths:
        paths = _expand(args.paths, recursive=args.recursive)
        empty_message = "No CSV files to check."
    else:
        paths = _csvs_in(INPUT_DIR, recursive=args.recursive)
        empty_message = "No CSV files in input_for_quote_check."
    if not paths:
        print(empty_message, file=sys.stderr)
        return 1

    scans: list[Scan] = []
    progress = False
    try:
        for path in paths:
            if not path.exists() and not path.is_file():
                scans.append(Scan(path=path, error="File not found."))
                continue

            def on_progress(rows: int, name: str = path.name) -> None:
                nonlocal progress
                progress = True
                print(
                    f"\r  reading {name}: {rows:,} data rows",
                    end="",
                    file=sys.stderr,
                    flush=True,
                )

            scans.append(
                scan_path(
                    path,
                    delimiter=delimiter,
                    quote=quote,
                    encoding=args.encoding,
                    header=not args.no_header,
                    max_rows=args.max_rows,
                    on_progress=on_progress,
                )
            )
    except KeyboardInterrupt:
        if progress:
            print(file=sys.stderr)
        print("Stopped.", file=sys.stderr)
        return 130

    if progress:
        print("\r" + " " * 78 + "\r", end="", file=sys.stderr)

    try:
        write_reports(scans, OUTPUT_DIR, max_rows=args.max_rows)
    except OSError as exc:
        reason = exc.strerror or str(exc)
        print(f"Could not write the report. {reason}", file=sys.stderr)
        return 1

    try:
        shown = str(OUTPUT_DIR.relative_to(Path.cwd()))
    except ValueError:
        shown = str(OUTPUT_DIR)
    body = render_terminal(scans).rstrip("\n")
    sys.stdout.write(f"{body}\n\nDetails written to {shown}\n")
    failed = any(scan.error for scan in scans)
    return 1 if failed else 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m quotes_checker",
        description=(
            "Read the CSV files in input_for_quote_check. "
            'Print which columns are written with quotes and which are not. '
            "Write the full detail for each file to output_for_quote_check."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  python -m quotes_checker\n"
            "  python -m quotes_checker data.csv\n"
            "  python -m quotes_checker folder_a folder_b\n"
        ),
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help=(
            "CSV files, folders, or a glob. "
            "Leave this out to read every CSV in input_for_quote_check."
        ),
    )
    parser.add_argument(
        "-r",
        "--recursive",
        action="store_true",
        help="when a path is a folder, include CSV files in subfolders",
    )
    parser.add_argument(
        "--no-header",
        action="store_true",
        help="treat the first row as data",
    )
    parser.add_argument(
        "--delimiter",
        help="force the delimiter: comma, pipe, tab, semicolon, or the character itself",
    )
    parser.add_argument(
        "--quote",
        help='force the quote mark: double, single, ", or \'',
    )
    parser.add_argument(
        "--encoding",
        help="force the file encoding, for example utf-8 or cp1252",
    )
    parser.add_argument(
        "--max-rows",
        type=int,
        default=0,
        help="stop each file after this many data rows (0 reads the whole file)",
    )
    return parser


def _parse_delim(value: str) -> str:
    key = value.lower()
    if key in _DELIMS:
        return _DELIMS[key]
    if len(value) == 1:
        return value
    raise ValueError(
        "Unknown delimiter. Use comma, pipe, tab, semicolon, or a single character."
    )


def _parse_quote(value: str) -> str:
    key = value.lower()
    if key in _QUOTES:
        return _QUOTES[key]
    if len(value) == 1:
        return value
    raise ValueError('Unknown quote mark. Use double, single, ", or \'.')


def _expand(raw_paths: list[str], *, recursive: bool) -> list[Path]:
    found: list[Path] = []
    for raw in raw_paths:
        path = Path(raw)
        if path.is_dir():
            found.extend(_csvs_in(path, recursive=recursive))
            continue
        if path.is_file():
            found.append(path)
            continue
        if any(char in raw for char in "*?["):
            matches = [
                match
                for match in sorted(Path().glob(raw), key=lambda item: str(item).lower())
                if match.is_file() and match.suffix.lower() == ".csv"
            ]
            found.extend(matches or [path])
            continue
        found.append(path)
    return found


def _csvs_in(folder: Path, *, recursive: bool) -> list[Path]:
    pattern = "**/*" if recursive else "*"
    files = [
        item
        for item in folder.glob(pattern)
        if item.is_file() and item.suffix.lower() == ".csv"
    ]
    return sorted(files, key=lambda item: str(item).lower())


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nStopped.", file=sys.stderr)
        raise SystemExit(130)
