"""Generate edge-case and large CSV pairs for the comparator tests."""

from __future__ import annotations

import argparse
import csv
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DS = ROOT / "TEST_DATA" / "DATASTG_DATA"
INFA = ROOT / "TEST_DATA" / "INFO_DATA"


def _ensure_dirs() -> None:
    DS.mkdir(parents=True, exist_ok=True)
    INFA.mkdir(parents=True, exist_ok=True)


def write_worked_example() -> None:
    """DESIGN.md section 15 — exact bytes."""
    ds = (
        b"CUST_ID,NAME,AMOUNT,CITY\n"
        b"101,nigam,10.00,Pune\n"
        b"102,asha,20.50,Mumbai\n"
        b"103,ravi,5.00,Delhi\n"
        b"103,ravi,5.00,Delhi\n"
        b"104,meera,7.25,Goa\n"
        b"106,rohit,10.000,Pune\n"
        b"107,sana,1.50,Nashik\n"
    )
    # Informatica: pipe, header cust_id|name |amount|city, record 106 city Pune + trailing space
    infa = (
        b"cust_id|name |amount|city\n"
        b"101|nigam|0000000010.00|Pune\n"
        b"102|asha|20.50|Mumbai\n"
        b"103|ravi|5.00|Delhi\n"
        b"104|Meera|7.25|Goa\n"
        b"105|kiran|3.00|Pune\n"
        b"106|Rohit|00010.000|Pune \n"
    )
    (DS / "customer_ds.csv").write_bytes(ds)
    (INFA / "customer_infa.csv").write_bytes(infa)


def write_columns_differ() -> None:
    (DS / "coldiff_ds.csv").write_text(
        "A,B,C\n1,2,3\n", encoding="utf-8"
    )
    (INFA / "coldiff_infa.csv").write_text(
        "A,B,D,E\n1,2,3,4\n", encoding="utf-8"
    )


def write_headers_case_space() -> None:
    (DS / "hdrspace_ds.csv").write_text(
        "CUST_ID,NAME\n1,a\n", encoding="utf-8"
    )
    (INFA / "hdrspace_infa.csv").write_text(
        "cust_id , name\n1,a\n", encoding="utf-8"
    )


def write_mixed_encoding() -> None:
    # Mostly UTF-8 with one cp1252 byte (0xE9 = é in cp1252) that is invalid alone in UTF-8
    # Use 0x80 which is invalid UTF-8 lead and defined in cp1252 as €
    ds = "ID,NAME\n1,café\n".encode("utf-8")
    # Invalid byte 0xE9 as sole character in name field (cp1252 é)
    infa = b"ID,NAME\n1,caf\xe9\n"
    (DS / "mixedenc_ds.csv").write_bytes(ds)
    (INFA / "mixedenc_infa.csv").write_bytes(infa)


def write_utf16_bom() -> None:
    text = "ID,NAME\n1,hello\n"
    (DS / "utf16le_ds.csv").write_bytes(b"\xff\xfe" + text.encode("utf-16-le"))
    (INFA / "utf16le_infa.csv").write_text(text, encoding="utf-8")
    (DS / "utf16be_ds.csv").write_bytes(b"\xfe\xff" + text.encode("utf-16-be"))
    (INFA / "utf16be_infa.csv").write_text(text, encoding="utf-8")


def write_utf16_no_bom() -> None:
    text = "ID,NAME\n1,hello\n"
    # LE without BOM — NULs in odd positions for ASCII
    (DS / "utf16nobom_ds.csv").write_bytes(text.encode("utf-16-le"))
    (INFA / "utf16nobom_infa.csv").write_text(text, encoding="utf-8")


def write_different_delim_quote() -> None:
    (DS / "delim_ds.csv").write_text(
        "ID;NAME\n1;a\n", encoding="utf-8"
    )
    # Use single-quote as quote char
    (INFA / "delim_infa.csv").write_text(
        "ID,NAME\n1,'a,b'\n", encoding="utf-8"
    )
    # Actually for matching columns and a fair pair, both should have same data shape.
    # DS semicolon, INFA comma with quoted comma inside — different row content OK for edge.
    # Better: same logical rows
    (DS / "delim_ds.csv").write_text('ID,NAME\n1,"a,b"\n', encoding="utf-8")
    (INFA / "delim_infa.csv").write_text("ID|NAME\n1|a,b\n", encoding="utf-8")


def write_quoted_newline_and_doubled_quotes() -> None:
    ds = 'ID,NOTE\n1,"line1\nline2"\n2,"say ""hi"""\n'
    infa = 'ID,NOTE\n1,"line1\nline2"\n2,"say ""hi"""\n'
    (DS / "quotes_ds.csv").write_text(ds, encoding="utf-8", newline="\n")
    (INFA / "quotes_infa.csv").write_text(infa, encoding="utf-8", newline="\n")


def write_malformed_below_limit() -> None:
    # 100 good rows + 1 bad (too few columns) → under 1%
    lines = ["ID,NAME"] + [f"{i},n{i}" for i in range(100)]
    ds = "\n".join(lines) + "\n"
    infa_lines = ["ID,NAME"] + [f"{i},n{i}" for i in range(100)]
    infa_lines.append("bad_only_one_field")
    (DS / "rejectok_ds.csv").write_text(ds, encoding="utf-8")
    (INFA / "rejectok_infa.csv").write_text("\n".join(infa_lines) + "\n", encoding="utf-8")


def write_malformed_above_limit() -> None:
    lines = ["ID,NAME"] + [f"{i},n{i}" for i in range(10)]
    bad = ["too,many,fields,here"] * 5
    (DS / "rejectbad_ds.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (INFA / "rejectbad_infa.csv").write_text(
        "\n".join(["ID,NAME"] + bad) + "\n", encoding="utf-8"
    )


def write_duplicate_rows() -> None:
    (DS / "duprows_ds.csv").write_text(
        "ID,V\n1,a\n1,a\n1,a\n2,b\n", encoding="utf-8"
    )
    (INFA / "duprows_infa.csv").write_text(
        "ID,V\n1,a\n2,b\n", encoding="utf-8"
    )


def write_manual_key_duplicates() -> None:
    (DS / "mankey_ds.csv").write_text(
        "ID,NAME,VAL\n1,a,10\n1,b,20\n2,c,30\n", encoding="utf-8"
    )
    (INFA / "mankey_infa.csv").write_text(
        "ID,NAME,VAL\n1,a,11\n1,b,21\n2,c,31\n", encoding="utf-8"
    )


def write_invalid_manual_key() -> None:
    (DS / "badkey_ds.csv").write_text("ID,NAME\n1,a\n", encoding="utf-8")
    (INFA / "badkey_infa.csv").write_text("ID,NAME\n1,a\n", encoding="utf-8")


def write_no_key_fallback() -> None:
    # No unique overlapping column
    (DS / "nokey_ds.csv").write_text(
        "A,B\n1,x\n2,y\n3,z\n", encoding="utf-8"
    )
    (INFA / "nokey_infa.csv").write_text(
        "A,B\n9,x\n8,y\n7,w\n", encoding="utf-8"
    )


def write_identical() -> None:
    body = "ID,NAME,AMT\n1,a,1.0\n2,b,2.0\n"
    (DS / "identical_ds.csv").write_text(body, encoding="utf-8")
    (INFA / "identical_infa.csv").write_text(body, encoding="utf-8")


def write_unpaired_file() -> None:
    (DS / "lonely_ds.csv").write_text("ID\n1\n", encoding="utf-8")
    # no infa partner


def write_ignored_file() -> None:
    (DS / "customer_ds (1).csv").write_text("ID\n1\n", encoding="utf-8")


def write_large_pair(
    rows: int = 10_000_000,
    cols: int = 30,
    *,
    all_mismatch: bool = False,
    prefix: str | None = None,
) -> None:
    """Generate large DS/INFA pair. all_mismatch differs in one column only."""
    prefix = prefix or ("large_mm" if all_mismatch else "large")
    ds_path = DS / f"{prefix}_ds.csv"
    infa_path = INFA / f"{prefix}_infa.csv"

    headers = ["ID"] + [f"C{i}" for i in range(1, cols)]
    header_line = ",".join(headers) + "\n"

    # Fast path: write raw lines without csv module overhead
    with ds_path.open("w", encoding="utf-8", newline="\n") as fds, infa_path.open(
        "w", encoding="utf-8", newline="\n"
    ) as fina:
        fds.write(header_line)
        fina.write(header_line)
        buf_ds: list[str] = []
        buf_infa: list[str] = []
        flush_every = 50_000
        for i in range(rows):
            # Compact fixed-width-ish values to limit disk use on large runs
            parts = [str(i)] + [f"{j}:{i}" for j in range(1, cols)]
            ds_line = ",".join(parts) + "\n"
            buf_ds.append(ds_line)
            if all_mismatch:
                parts[1] = f"X:{i}"
                buf_infa.append(",".join(parts) + "\n")
            elif i % 1000 == 0:
                parts[1] = f"D:{i}"
                buf_infa.append(",".join(parts) + "\n")
            else:
                buf_infa.append(ds_line)
            if len(buf_ds) >= flush_every:
                fds.write("".join(buf_ds))
                fina.write("".join(buf_infa))
                buf_ds.clear()
                buf_infa.clear()
        if buf_ds:
            fds.write("".join(buf_ds))
            fina.write("".join(buf_infa))


def write_all_edge_cases() -> None:
    _ensure_dirs()
    write_worked_example()
    write_columns_differ()
    write_headers_case_space()
    write_mixed_encoding()
    write_utf16_bom()
    write_utf16_no_bom()
    write_different_delim_quote()
    write_quoted_newline_and_doubled_quotes()
    write_malformed_below_limit()
    write_malformed_above_limit()
    write_duplicate_rows()
    write_manual_key_duplicates()
    write_invalid_manual_key()
    write_no_key_fallback()
    write_identical()
    write_unpaired_file()
    write_ignored_file()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--all-edge", action="store_true")
    p.add_argument("--large", action="store_true")
    p.add_argument("--large-mismatch", action="store_true")
    p.add_argument("--rows", type=int, default=10_000_000)
    p.add_argument("--cols", type=int, default=30)
    args = p.parse_args(argv)
    _ensure_dirs()
    if args.all_edge or not (args.large or args.large_mismatch):
        write_all_edge_cases()
        print("Wrote edge-case pairs")
    if args.large:
        print(f"Writing large pair ({args.rows} x {args.cols})...")
        write_large_pair(args.rows, args.cols, all_mismatch=False)
        print("Done large")
    if args.large_mismatch:
        print(f"Writing large all-mismatch pair ({args.rows} x {args.cols})...")
        write_large_pair(args.rows, args.cols, all_mismatch=True)
        print("Done large mismatch")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
