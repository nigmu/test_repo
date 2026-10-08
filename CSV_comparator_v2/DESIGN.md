# CSV–CSV comparator

Design for comparing large DataStage and Informatica CSV extracts, about 10 million rows per file, on a machine with 10 GB RAM and a 2.69 GHz base processor. Other applications run on the same machine and can use up to about 7 GB under load. Python, with libraries installed through pip only.

The tool must finish each pair in minutes, not overnight, and must not crash or silently give wrong results when a file is large, oddly formatted, or has mixed encodings.

## 1. Goal

For each pair of files (one DataStage, one Informatica):

1. Check that both files have the same columns. If they do not, stop that pair and report it.
2. Compare the files as multisets of whole rows. A row matches only when every cell is identical, character for character.
3. For rows that do not match, find the matching record on the other side through a key column (detected automatically or set by hand) and report which cells differ, in the form: column, Informatica value, DataStage value.
4. When no key exists, report the unmatched rows as whole rows instead.
5. Write one Excel workbook per pair (summary and samples) and CSV files with the complete lists.

## 2. Inputs and file pairing

| Role | Folder | Name pattern |
| --- | --- | --- |
| DataStage | `TEST_DATA\DATASTG_DATA` | `<prefix>_ds.csv` |
| Informatica | `TEST_DATA\INFO_DATA` | `<prefix>_infa.csv` |

- `customer_ds.csv` is compared with `customer_infa.csv`.
- Suffix and prefix matching is case-insensitive, because Windows file names are. `Customer_DS.csv` pairs with `customer_infa.csv`.
- The prefix is everything before the final `_ds.csv` or `_infa.csv`. `my_ds_data_ds.csv` has the prefix `my_ds_data`.
- Files that do not end in either suffix (for example `customer_ds (1).csv`) are ignored and listed in the run log.
- Files that have no partner are logged and skipped.
- Pairs are processed one at a time, in prefix order.

## 3. Processing steps for one pair

1. Check free disk space.
2. Detect the encoding of each file. Transcode to UTF-8 only when needed (section 4).
3. Detect the CSV format of each file separately: delimiter, quote character, escape character (section 5).
4. Read the header row of each file and compare the columns (section 6).
5. If the columns differ: count the rows of each file, write a Summary-only workbook, and stop this pair.
6. Load each file into an on-disk DuckDB table, all values as text, with a hash of each full row (section 7).
7. Row comparison: matched rows, rows only on one side, and extra copies (section 9).
8. Column analysis: per-column value differences and data-type labels (section 10).
9. Key selection: manual override, automatic detection, or none (section 11).
10. With a key: pair the unmatched rows and list the differing cells. Without a key: list the unmatched rows whole (section 12).
11. Write the workbook and the CSV files (section 13).
12. Delete the temporary files for this pair.

## 4. Encoding

Most files are UTF-8. Some are mostly UTF-8 with a few bytes in another encoding. The fallback for those bytes is cp1252 (Windows Western European).

Encoding is decided per file. The two files in a pair can end up with different encodings.

### 4.1 Detection order

1. **Byte-order mark (BOM).**
   - `EF BB BF` means UTF-8 with a BOM. Continue with step 3.
   - `FF FE` means UTF-16 little-endian. `FE FF` means UTF-16 big-endian. Go to step 2.
2. **UTF-16.** The file is transcoded to a temporary UTF-8 file. A UTF-16 file without a BOM is recognised when NUL bytes fill every other position in the first 4 KB, and is transcoded the same way. A decode error in a UTF-16 file stops the pair with an error.
3. **UTF-8 check.** The file is read in binary chunks (16 MB by default) through Python's incremental UTF-8 decoder (`codecs.getincrementaldecoder`). The incremental decoder is required so a multi-byte character split across two chunks is not reported as an error. Nothing is written in this pass.
   - If the whole file is valid UTF-8, DuckDB reads the original file directly. No copy is made.
   - At the first invalid byte, the check stops and step 4 runs.
4. **Mixed file.** The file is transcoded to a temporary UTF-8 file. Valid UTF-8 is kept as is. Only the invalid bytes are decoded as cp1252, through a custom error handler registered with `codecs.register_error`. cp1252 leaves five bytes undefined (`0x81`, `0x8D`, `0x8F`, `0x90`, `0x9D`); those are decoded as Latin-1, which maps each byte to the character with the same number. The transcoder counts the fallback bytes and records the line numbers of the first 10.

A whole-file fallback (decoding the entire file as cp1252 when one byte is invalid) is not used. It would turn every legitimate UTF-8 character in the file into garbled text such as `Ã©`, and every row containing one would then mismatch against the clean side.

Transcoding never adds or removes line breaks, so line numbers in the transcoded file are the same as in the original.

### 4.2 Reporting

The Summary shows one of these for each file:

- `UTF-8`
- `UTF-8 (BOM)`
- `UTF-8 with 3 bytes decoded as cp1252 (first at lines 88, 412, 9031)`
- `UTF-16LE (BOM)`, `UTF-16BE (BOM)`, `UTF-16LE (no BOM, detected from NUL pattern)`

A pair where either file needed the cp1252 fallback also gets a warning line on the Summary, so a mismatch caused by encoding is not mistaken for a data difference.

After decoding, the character-for-character match rule applies unchanged.

## 5. CSV format

The two files in a pair can use different delimiters and quote characters, and neither is known in advance.

- The format of each file is detected separately with DuckDB's `sniff_csv`, on the UTF-8 file DuckDB will read (the original or the transcoded copy). The sniffer reads a sample of rows, 20,480 by default. Sniffer columns include Delimiter, Quote, Escape, NewLineDelimiter, Comment, SkipRows, HasHeader, Columns, DateFormat, TimestampFormat, UserArguments, Prompt.
- When the sniffer returns Quote or Escape as the string `(empty)`, DuckDB `read_csv` fails if `escape=''` is passed. The tool then uses quote `"` and escape `"` (doubled-quote style) and notes that on the Summary. A pipe-delimited file still reads correctly with quote `"`.
- The detected delimiter, quote character and escape character are written on the Summary for each file.
- `configs.py` can override the format for a given prefix and side (`FORMAT_OVERRIDES`). Overrides take priority over detection.
- The first record of every file is the header. Header detection from the sniffer is not used.

A wrongly detected delimiter almost always produces the wrong column count, which the column check in section 6 catches, stopping the pair safely. A wrongly detected quote character usually produces many malformed rows, which the reject limit in section 7.3 catches.

## 6. Headers and the column check

The header row of each file is read in Python with the `csv` module, using the detected delimiter and quote character. This avoids DuckDB renaming duplicate or unusual column names. A leading BOM character (`U+FEFF`) is removed from the first header cell.

**Columns match** when:

1. both files have the same number of columns, and
2. at every position, the two header names are equal after removing leading and trailing whitespace and ignoring letter case.

`CUST_ID` matches `cust_id `. Columns are compared by position: the same names in a different order count as a difference.

**When the columns do not match**, the pair stops before any data comparison. Each file is still read once to count its data rows and malformed lines. The workbook contains only the Summary sheet (section 13.3), and no CSV files are written.

Header names are shown as written in the DataStage file throughout the outputs. If two header names in a file are equal after trimming and ignoring case, the outputs make them unique by adding the column position, for example `AMOUNT (3)` and `AMOUNT (7)`.

## 7. Reading the data

### 7.1 Reader settings

Each file is loaded once into a table in an on-disk DuckDB database (`OUTPUT\_tmp\<prefix>.duckdb`). All later steps read these tables, which is much faster than parsing the CSV text again for every step.

DuckDB `read_csv` settings:

| Setting | Value | Reason |
| --- | --- | --- |
| `all_varchar` | `true` | Every cell is read as text. `0000000100.00` stays `0000000100.00`. Nothing is cast. |
| `delim`, `quote`, `escape` | From section 5 | Detected or overridden per file |
| `header` | `true` | The header row is not data |
| `names` | `c1` … `cN` | Positional internal names. The real header text is only used for display. |
| `store_rejects` | `true` | Malformed lines are recorded, not silently dropped, and do not stop the load (except `max_line_size` — see 7.3) |
| `null_padding` | `false` | Short rows are rejects, not padded with empty columns |
| `max_line_size` | From `configs.py` | Longest allowed line; longer lines abort the scan |

Empty fields: the reader returns `NULL` for an empty field and for a quoted empty field (`""`). At load time every `NULL` is replaced with an empty string (`coalesce(c, '')`). An empty field and `""` are both the empty string, because quoting is CSV syntax and not part of the cell. A field containing only a space stays a single space.

This matters for correctness. In SQL, `NULL = NULL` is not true. Without the replacement, two identical rows with a blank cell would never match.

### 7.2 Row hash

At load time each row also gets a hash of its full cell sequence:

`md5_number(concat(strlen(c1), ':', c1, strlen(c2), ':', c2, …, strlen(cN), ':', cN))`

- `strlen` is the length in **bytes** (not characters; use `length()` for character count). The design hashes with `strlen`.
- `md5_number` returns DuckDB type **UHUGEINT** (unsigned 128-bit). It is treated as the row identity for grouping and joins.

Each cell is prefixed with its length in bytes. That makes the text being hashed unambiguous: the rows `a,bc` and `ab,c` produce different text (`1:a2:bc` and `2:ab1:c`) no matter what characters the cells contain.

Two different rows could in theory produce the same 128-bit hash. For 20 million distinct rows the probability is below 10⁻²⁴, so the hash is treated as exact.

### 7.3 Malformed lines

Lines with the wrong number of fields or unterminated quotes are rejected by DuckDB when `store_rejects=true` and recorded in its reject tables with the line number, error type and raw line. `null_padding` must stay `false` so short rows become rejects instead of being padded with NULLs.

**`max_line_size` is different.** A line longer than `max_line_size` **aborts the scan** even with `store_rejects=true`. Those lines do **not** land in the reject table. The tool catches the DuckDB error and stops the pair with that message on the Summary.

- Rejected lines (wrong field count, etc.) are excluded from the comparison and counted separately on the Summary as **distinct file line numbers** (one bad line can produce more than one reject row).
- They are listed on the Rejected lines sheet (sample) and in `<prefix>_comparison_rejected.csv` (all). The `csv_line` value often has a leading newline; it is stripped for display.
- If rejected lines exceed `REJECT_LIMIT_PERCENT` of a file's lines (1% by default), the pair stops with the status "Too many malformed lines". That many rejects usually means the format was detected wrong, and comparing the remaining rows would be misleading.

### 7.4 Self-test at startup

Before the first pair, the tool writes a small built-in CSV file and loads it with the exact reader settings above. The file covers:

- leading and trailing spaces
- an empty field, a quoted empty field, and a space-only field
- leading and trailing zeros (`0000000100.00`, `10.000`)
- a delimiter inside a quoted field
- a line break inside a quoted field
- a doubled quote inside a quoted field
- non-ASCII characters

Every loaded value is checked against the expected text. If any check fails, the run stops before touching the real files. This protects against a DuckDB version that trims, casts, or treats empty values differently.

## 8. Match rule

A row matches only when the same full cell sequence exists on the other side, character for character.

- `10.00` and `0000000100.00` do not match.
- A leading or trailing space does not match.
- Leading zeros, trailing zeros, and letter case stay as written.
- Nothing is trimmed, cast, or reformatted before the compare.
- An empty field is an empty string. A field that is only a space is a space. They are not equal.
- Quoted CSV syntax is not part of the cell. The characters inside the field, after parsing, are what must match.

Copy counts matter. Three identical copies on one side and one copy on the other count as one matched row and two extra copies. The extra copies are mismatches.

## 9. Row comparison

Each table is grouped by row hash to count copies. The two grouped tables are full-outer-joined on the hash. For every distinct row this gives a DataStage copy count and an Informatica copy count (either can be zero).

| Result | Rule |
| --- | --- |
| Matched rows | Sum over all hashes of the smaller of the two copy counts |
| Rows only in DataStage | Sum of DataStage copies for hashes that have no Informatica copy |
| Rows only in Informatica | Sum of Informatica copies for hashes that have no DataStage copy |
| Extra copies in DataStage | Sum of (DataStage copies − Informatica copies) for hashes on both sides where the DataStage count is larger |
| Extra copies in Informatica | The same, the other way round |
| Distinct rows | Number of distinct hashes in a file |
| Match rate | Matched rows ÷ the larger of the two row counts × 100 |

For each file: row count = matched rows + rows only in that file + extra copies in that file.

Only the hash and the two counts take part in the grouping and the join. That is about 10 million × 24 bytes, roughly 250 MB per file, instead of the full row text, so this step fits in the memory cap with little or no spilling.

The files are never sorted for the comparison. Grouping plus a join gives the same result as sorting both files and merging them, and it costs much less. Sorting is used only to order the Excel samples (section 13.2).

Rows are never compared by row number. One extra row, or one difference in an early column, would shift every later row and mark identical rows as mismatches.

## 10. Column analysis

This runs for every pair whose columns match. It shows why rows mismatch even when all 10 million do, and it provides the statistics used by key detection.

### 10.1 Column differences

For each column, the distinct values of the DataStage file are compared with the distinct values of the Informatica file:

- distinct value count on each side
- number of values present only in DataStage, and only in Informatica
- up to `EXAMPLE_VALUES` examples of each (3 by default), in text sort order
- after pairing (section 12): how many paired records differ in this column

A column with zero values on only one side is not causing any mismatches.

### 10.2 Data types

Types are never used for sorting or matching. They are only reported.

Each non-empty cell is classified from its raw text:

| Label | Rule | Examples |
| --- | --- | --- |
| Integer-like | Optional `-`, then one or more digits | `10`, `-3`, `007` |
| Decimal-like | Optional `-`, then digits with one `.`, with at least one digit before or after it | `10.00`, `0000000100.00`, `.5`, `10.` |
| Text | Anything else | `abc`, `+5`, `1e5`, `1,000`, ` 10`, `2024-01-31` |

Empty cells are not classified, so a nullable number column is not labelled Mixed because of its blanks.

Column label, separately for each file:

| Column label | When |
| --- | --- |
| Integer-like, Decimal-like, or Text | Every non-empty cell has that label |
| Mixed | Non-empty cells have more than one label |
| Empty | The column has no non-empty cells |

The Column differences sheet shows the label and the count of cells of each kind for both files. A column whose label differs between the files is highlighted.

`10` against `10.00` is a type difference (Integer-like against Decimal-like) and also a mismatch. `10.00` against `0000000100.00` has the same label, so there is no type difference, but it is still a mismatch because the characters differ.

## 11. Key selection

A key is one or more columns that identify a record, so a DataStage row can be paired with its Informatica row. Without a key, the tool can only say that a row does not exist on the other side. With one, it can say which cells differ.

### 11.1 Choosing the mode

For each pair, in this order:

1. If the prefix is in `KEY_OVERRIDES` with a list of columns, that list is the key (manual).
2. If the prefix is in `KEY_OVERRIDES` with the value `None`, no key is used and the pair uses full-row output.
3. Otherwise `KEY_MODE` applies: `"auto"` (default) runs detection, and `"none"` uses full-row output.

### 11.2 Manual key

- Column names are matched against the headers with the same rule as the column check: trimmed, ignoring case.
- A name that matches no column, or matches more than one column, stops the pair with the status "Invalid key in configs.py" and the reason. The tool does not quietly fall back to automatic detection.
- A manual key does not have to be unique. Keys that belong to more than one distinct unmatched row on a side cannot be paired; those rows go to the unpaired list with the status "Duplicate key", and the Summary shows how many there were.

### 11.3 Automatic detection

**Definitions**

- A column's **overlap** is the number of distinct values found on both sides ÷ the larger of the two distinct counts. The same definition applies to a combination of columns, using distinct value combinations.
- A column or combination is **unique** in a file when no two different rows share the same value: its distinct count equals the file's distinct row count. Exact duplicate rows share a key but are the same row, so they do not break uniqueness.

**Steps**

1. **Candidates** are columns whose overlap is at least `KEY_MIN_OVERLAP` (0.80 by default). This rules out columns written differently on the two sides, such as an ID written `00101` on one side and `101` on the other; pairing on them would pair almost nothing.
2. **Single column.** Candidates that are unique in both files qualify. If several qualify, the tool chooses in this order: a header matching one of `KEY_NAME_HINTS` (such as `ID`, `KEY`, `NO`) as a word, then higher overlap, then the leftmost column. Single-column overlap and uniqueness come straight from the column analysis, so this step costs almost nothing.
3. **Combinations.** If no single column qualifies, the tool takes the top `KEY_CANDIDATE_LIMIT` candidates (5 by default), ranked by distinct count, and tries every pair of them, then every triple, up to `KEY_MAX_COLUMNS` columns (3 by default). That is at most 20 combinations. For each one, in order:
   1. A quick screen with DuckDB's `approx_count_distinct`, which uses very little memory. A combination whose approximate distinct count is clearly below the distinct row count of either file is skipped.
   2. An exact uniqueness check in both files.
   3. An exact overlap check against `KEY_MIN_OVERLAP`.

   The first combination that passes all three becomes the key.
4. **Fallback.** If nothing qualifies, the pair uses full-row output and the Summary gives the reason.

**Risk.** A column can be unique by coincidence, such as a timestamp or an amount, and still pass the overlap check. The pairs would then join unrelated records. The Summary always shows the chosen key and its statistics so this is easy to spot, and `KEY_OVERRIDES` fixes it for that file.

## 12. Pairing and cell differences

### 12.1 With a key

Pairing only looks at rows that exist on one side only, that is, hashes with a zero copy count on the other side. Matched rows are already accounted for.

1. DataStage-only distinct rows and Informatica-only distinct rows are joined on the key columns.
2. For an automatic key, uniqueness guarantees each key belongs to at most one distinct row per side, so every pair is one-to-one.
3. For every pair, each non-key column whose values differ produces one output line: key values, column name, Informatica value, DataStage value, and the copy count on each side. A paired record always differs in at least one non-key column, because otherwise the two rows would have the same hash.
4. Everything that did not pair goes to the unpaired list:

| Status | Meaning |
| --- | --- |
| Only in DataStage | No Informatica row with the same key |
| Only in Informatica | No DataStage row with the same key |
| Extra copies in DataStage | The row exists on both sides, but more times in DataStage |
| Extra copies in Informatica | The same, the other way round |
| Duplicate key in DataStage | Manual key only: the key belongs to more than one DataStage-only row |
| Duplicate key in Informatica | Manual key only: the same, for Informatica |

### 12.2 Without a key (fallback)

Every distinct row that is not fully matched is listed once, with its status (Only in DataStage, Only in Informatica, Extra copies in DataStage or Informatica), the copy count on each side, the number of unmatched copies, and all its cells in the original column order.

### 12.3 Locating records

Data rows are identified by their key values, or in fallback by their full content. File line numbers are not reported for data rows, because DuckDB's parallel CSV reader does not keep them. Line numbers are reported for rejected lines, which DuckDB records itself.

## 13. Outputs

### 13.1 Files

All in `OUTPUT\`:

| File | Written when | Contents |
| --- | --- | --- |
| `<prefix>_comparison.xlsx` | Always | Summary, analysis, and samples (section 13.2) |
| `<prefix>_comparison_differences.csv` | Key used, at least one differing cell | Every differing cell |
| `<prefix>_comparison_unpaired.csv` | Key used, at least one unpaired row | Every unpaired row |
| `<prefix>_comparison_mismatches.csv` | No key, at least one mismatch | Every unmatched row, whole |
| `<prefix>_comparison_rejected.csv` | At least one rejected line | Every rejected line |
| `run_log.txt` | Always | One entry per pair and per skipped or ignored file |

**The workbook is the report a person reads. The CSV files are the complete lists.** Excel sheets hold at most `EXCEL_SAMPLE_ROWS` data rows (100,000 by default; the maximum is 1,048,575 because the header uses one of Excel's 1,048,576 rows). The CSV files always hold everything.

CSV format for all output files:

- UTF-8, comma delimiter, header row
- standard CSV quoting: a value is quoted when it contains a comma, a double quote, or a line break
- values exactly as compared, with no added quotes, so trailing spaces and leading zeros are kept
- written directly by DuckDB (`COPY (query) TO ...`), which streams rows to disk without passing them through Python
- row order is not guaranteed

Opening a CSV by double-clicking it in Excel changes values on screen: `0000000010.00` becomes `10`, leading zeros vanish, and long numbers turn into scientific notation. The file itself is correct. To view it in Excel without this, use Data → From Text/CSV and set every column to Text. The Summary sheet repeats this note.

In the worst case, where every row mismatches in many columns, the differences CSV holds one line per differing cell. For 10 million records with 30 differing columns each that is 300 million lines, many gigabytes. The Summary shows the line count of every CSV.

### 13.2 Workbook sheets when the columns match

| Sheet | Present when | Contents |
| --- | --- | --- |
| Summary | Always | Section 14 |
| Column differences | Always | Section 10, one row per column |
| Differences (sample) | Key used | Cell-level differences |
| Unpaired rows (sample) | Key used | Unpaired rows, whole |
| Mismatches (sample) | No key | Unmatched rows, whole |
| Rejected lines (sample) | At least one rejected line | File, line number, error, raw line |

When a pair matches completely, the sample sheets contain a single line saying "No differences".

**Differences (sample)** columns: one column per key column, then `Column`, `INFA`, `DS`, `INFA copies`, `DS copies`. Rows are sorted by the key values, then by column position.

**Unpaired rows (sample)** and **Mismatches (sample)** columns: `Status`, `DS copies`, `INFA copies`, `Unmatched copies`, then every column in the original order. Rows are sorted by all columns left to right, then by status, so the DataStage and Informatica versions of similar rows sit next to each other.

Sorting uses raw text order, the same on both sides. `10` sorts before `2`, and uppercase letters sort before lowercase. Each sample is taken with `ORDER BY … LIMIT EXCEL_SAMPLE_ROWS`, which DuckDB runs without sorting the full result.

Excel cell rules:

- Every data cell is written as text, so Excel does not turn `0000000100.00` into `100`.
- When `EXCEL_QUOTE_VALUES` is on (default), values in the `INFA` and `DS` columns of the Differences sheet and in the example lists of the Column differences sheet are shown in single quotes (`'Pune '`) so leading and trailing spaces are visible. The CSV files never add quotes.
- Excel allows at most 32,767 characters in a cell. A longer value is cut and ends with `…[truncated]`. The Summary counts truncated cells. The CSV files are never truncated.
- The workbook is written with `xlsxwriter` in `constant_memory` mode, which streams rows to disk. Rows are fetched from DuckDB in batches of `FETCH_BATCH_ROWS`, never all at once.

### 13.3 Workbook when the columns do not match

Only the Summary sheet. It includes, for each file, the row count, the rejected line count, the column count, and a table of every header position:

| Position | DataStage header | Informatica header | Status |
| --- | --- | --- | --- |
| 1 | CUST_ID | cust_id | Match |
| 2 | NAME | name | Match |
| 3 | AMOUNT | load_date | Different |
| 4 | CITY | amount | Different |
| 5 | | city | Extra in Informatica |

## 14. Summary contents

**Per file (DataStage and Informatica side by side)**

- File name
- Encoding (section 4.2)
- Delimiter, quote character, escape character, and whether each was detected or set in `configs.py`
- Data rows (header excluded, rejected lines excluded)
- Rejected lines
- Distinct rows
- Column count

**Result**

- Overall result: `MATCH` (identical as multisets of rows), `MISMATCH`, `COLUMNS DIFFER – comparison stopped`, or `ERROR` with the reason
- Columns match: Yes or No (ignoring case and surrounding whitespace)
- Matched rows
- Rows only in DataStage, rows only in Informatica
- Extra copies in DataStage, extra copies in Informatica
- Match rate
- Warnings: cp1252 fallback used, rejected lines present, Excel cells truncated

**Key**

- Key source: Manual (configs.py), Auto-detected, or None
- Key columns
- Uniqueness in each file: distinct key values ÷ distinct rows
- Overlap of key values
- Paired records and differing cells
- Unpaired rows by status
- Fallback reason, when no key was used

**Outputs**

- For each sheet: rows shown and total, for example `100,000 of 29,944,012 (truncated, full list in customer_comparison_differences.csv)`
- For each CSV file: file name and line count
- The note about opening CSV files in Excel (section 13.1)

**Timing**

- Time per step: encoding check, format detection, load, row comparison, column analysis, key selection and pairing, output writing
- Total runtime

## 15. Worked example

`customer_ds.csv` (comma-delimited):

```text
CUST_ID,NAME,AMOUNT,CITY
101,nigam,10.00,Pune
102,asha,20.50,Mumbai
103,ravi,5.00,Delhi
103,ravi,5.00,Delhi
104,meera,7.25,Goa
106,rohit,10.000,Pune
107,sana,1.50,Nashik
```

`customer_infa.csv` (pipe-delimited, lowercase headers, and a trailing space after `Pune` for record 106):

```text
cust_id|name |amount|city
101|nigam|0000000010.00|Pune
102|asha|20.50|Mumbai
103|ravi|5.00|Delhi
104|Meera|7.25|Goa
105|kiran|3.00|Pune
106|Rohit|00010.000|Pune 
```

**Columns.** Four on each side, and the names are equal after trimming and ignoring case (`name ` equals `NAME`), so the comparison continues.

**Row comparison.** 102 matches once and 103 matches once, so 2 rows match. DataStage has one extra copy of 103. Rows only in DataStage: 101, 104, 106, 107. Rows only in Informatica: 101, 104, 105, 106. Match rate: 2 ÷ 7 = 28.57%.

**Key detection.**

| Column | Overlap | Unique in both files | Result |
| --- | --- | --- | --- |
| CUST_ID | 5 of 6 = 83.33% | Yes (6 values, 6 distinct rows on each side) | Candidate, chosen |
| NAME | 3 of 6 = 50% | – | Below 80%, not a candidate |
| AMOUNT | 3 of 6 = 50% | – | Below 80%, not a candidate |
| CITY | 4 of 5 = 80% | No (5 values, 6 distinct rows) | Candidate, not unique |

The two copies of 103 in DataStage are the same row, so they do not break the uniqueness of `CUST_ID`.

**Pairing.** Records 101, 104 and 106 pair. 107 (DataStage) and 105 (Informatica) have no partner. The extra copy of 103 is unpaired.

### Summary (extract)

| Item | DataStage | Informatica |
| --- | --- | --- |
| File | `customer_ds.csv` | `customer_infa.csv` |
| Encoding | UTF-8 | UTF-8 |
| Delimiter / quote | `,` / `"` (detected) | `\|` / `"` (detected) |
| Data rows | 7 | 6 |
| Rejected lines | 0 | 0 |
| Distinct rows | 6 | 6 |
| Columns | 4 | 4 |

| Result | Value |
| --- | --- |
| Overall result | MISMATCH |
| Columns match | Yes |
| Matched rows | 2 |
| Rows only in DataStage | 4 |
| Rows only in Informatica | 4 |
| Extra copies in DataStage | 1 |
| Extra copies in Informatica | 0 |
| Match rate | 28.57% |
| Key source | Auto-detected |
| Key columns | CUST_ID |
| Key uniqueness | DataStage 6 / 6, Informatica 6 / 6 |
| Key overlap | 83.33% |
| Paired records | 3 |
| Differing cells | 5 |
| Unpaired rows | 1 only in DataStage, 1 only in Informatica, 1 extra copy in DataStage |

### Column differences

| Column | DS type | INFA type | DS distinct | INFA distinct | Only in DS | Only in INFA | Paired records differing | DS-only examples | INFA-only examples |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| CUST_ID | Integer-like | Integer-like | 6 | 6 | 1 | 1 | Key | `'107'` | `'105'` |
| NAME | Text | Text | 6 | 6 | 3 | 3 | 2 | `'meera'`, `'rohit'`, `'sana'` | `'Meera'`, `'Rohit'`, `'kiran'` |
| AMOUNT | Decimal-like | Decimal-like | 6 | 6 | 3 | 3 | 2 | `'1.50'`, `'10.00'`, `'10.000'` | `'0000000010.00'`, `'00010.000'`, `'3.00'` |
| CITY | Text | Text | 5 | 5 | 1 | 1 | 1 | `'Nashik'` | `'Pune '` |

### Differences (sample)

| CUST_ID | Column | INFA | DS | INFA copies | DS copies |
| --- | --- | --- | --- | --- | --- |
| 101 | AMOUNT | `'0000000010.00'` | `'10.00'` | 1 | 1 |
| 104 | NAME | `'Meera'` | `'meera'` | 1 | 1 |
| 106 | NAME | `'Rohit'` | `'rohit'` | 1 | 1 |
| 106 | AMOUNT | `'00010.000'` | `'10.000'` | 1 | 1 |
| 106 | CITY | `'Pune '` | `'Pune'` | 1 | 1 |

### Unpaired rows (sample)

| Status | DS copies | INFA copies | Unmatched copies | CUST_ID | NAME | AMOUNT | CITY |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Extra copies in DataStage | 2 | 1 | 1 | 103 | ravi | 5.00 | Delhi |
| Only in Informatica | 0 | 1 | 1 | 105 | kiran | 3.00 | Pune |
| Only in DataStage | 1 | 0 | 1 | 107 | sana | 1.50 | Nashik |

### `customer_comparison_differences.csv`

The same five lines as the Differences sheet, without the added quotes. The last line ends in `Pune` followed by a space.

```text
CUST_ID,Column,INFA,DS,INFA copies,DS copies
101,AMOUNT,0000000010.00,10.00,1,1
104,NAME,Meera,meera,1,1
106,NAME,Rohit,rohit,1,1
106,AMOUNT,00010.000,10.000,1,1
106,CITY,Pune ,Pune,1,1
```

### Same files without a key (fallback)

If `KEY_OVERRIDES` set `"customer": None`, the Mismatches (sample) sheet would be:

| Status | DS copies | INFA copies | Unmatched copies | CUST_ID | NAME | AMOUNT | CITY |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Only in Informatica | 0 | 1 | 1 | 101 | nigam | 0000000010.00 | Pune |
| Only in DataStage | 1 | 0 | 1 | 101 | nigam | 10.00 | Pune |
| Extra copies in DataStage | 2 | 1 | 1 | 103 | ravi | 5.00 | Delhi |
| Only in Informatica | 0 | 1 | 1 | 104 | Meera | 7.25 | Goa |
| Only in DataStage | 1 | 0 | 1 | 104 | meera | 7.25 | Goa |
| Only in Informatica | 0 | 1 | 1 | 105 | kiran | 3.00 | Pune |
| Only in Informatica | 0 | 1 | 1 | 106 | Rohit | 00010.000 | Pune  |
| Only in DataStage | 1 | 0 | 1 | 106 | rohit | 10.000 | Pune |
| Only in DataStage | 1 | 0 | 1 | 107 | sana | 1.50 | Nashik |

## 16. Memory, speed and disk

### 16.1 Memory

Total RAM is 10 GB, and other applications can use about 7 GB under load, so only about 3 GB can be relied on.

- Neither file is ever loaded into Python memory. A pandas table of millions of rows of Python strings would need several times the CSV size.
- DuckDB `memory_limit` is set at the start of each pair to half of the memory available at that moment (measured with `psutil`), capped at `MEMORY_LIMIT_MAX_GB` (2 GB) and never below `MEMORY_LIMIT_MIN_GB` (1 GB).
- `threads` is `min(4, number of CPU cores)` unless `THREADS` is set. Each thread doing a join or grouping needs its own memory, so more threads with a small memory cap means more spilling to disk, not more speed.
- `preserve_insertion_order` is `false`, which lowers memory use on large loads. No step relies on insertion order.
- When DuckDB reaches the cap, it spills to `OUTPUT\_tmp` (`temp_directory`) instead of failing.
- Python-side memory stays small: the Excel writer streams (`constant_memory`), results are fetched in batches, and the CSV outputs never pass through Python.

### 16.2 Speed

The goal is minutes per pair. Where the time goes:

| Step | Cost |
| --- | --- |
| UTF-8 check | One sequential read of each file in Python. Decoding runs in C and is fast. |
| Transcoding | Only for mixed or UTF-16 files. One more read and write, single-threaded. |
| Load | One parallel DuckDB parse of each file. The largest single step for clean files. |
| Row comparison | Groups and joins 128-bit hashes only. Fast. |
| Column analysis | One distinct-value comparison per column over one column at a time, plus one scan per file for type counts. Grows with the number of high-cardinality columns. |
| Key detection | Nearly free for a single-column key. At most 20 combination checks otherwise. |
| Pairing and CSV output | One join plus a streamed write by DuckDB. Large when everything mismatches, because the output is large. |
| Excel samples | Python loop, capped at `EXCEL_SAMPLE_ROWS` per sheet. With 100,000 rows this takes seconds to a minute; with 1,048,575 rows it takes several minutes. |

Temp disk speed matters: spilling and transcoding are much slower on a hard disk than on an SSD. The time for each step is on the Summary, so slow steps are easy to identify.

### 16.3 Disk

- Before each pair, free space on the output drive is checked against `DISK_FREE_FACTOR` × (DataStage file size + Informatica file size), 4 by default. If there is not enough, the pair stops with the status "Not enough disk space".
- Temporary files (transcoded copies, the DuckDB database, spill files) live in `OUTPUT\_tmp` and are deleted when the pair finishes.
- The differences CSV can be many gigabytes in the worst case (section 13.1).

## 17. Reliability and error handling

- **One pair failing does not stop the run.** Each pair runs inside its own error handler. On an error the tool still tries to write a Summary-only workbook with the result `ERROR` and the message, records it in `run_log.txt`, cleans up, and moves to the next pair.
- **Out of memory.** If DuckDB still reports out of memory, the pair is retried once with `threads = 1`. If it fails again, it is reported as an error.
- **Output file open in Excel.** Outputs are written under a temporary name and renamed at the end. If the target file is locked, the result is saved as `<prefix>_comparison_<yyyyMMdd_HHmmss>.xlsx` (and the same for the CSV files), and the run log says so.
- **Temporary files.** They are removed in a `finally` block after each pair, unless `KEEP_TEMP_ON_ERROR` is on and the pair failed. `OUTPUT\_tmp` is also emptied at startup, in case a previous run was killed.
- **No silent data loss.** Malformed lines are counted and listed (section 7.3). cp1252 fallback bytes are counted and located (section 4). Truncated Excel cells are counted (section 13.2).
- **Run log.** For each pair: start and end time, result, row counts, output files written, and the error message if any. It also lists ignored files and files without a partner.

## 18. Configuration (`configs.py`)

| Setting | Default | Meaning |
| --- | --- | --- |
| `DS_FOLDER` | `TEST_DATA\DATASTG_DATA` | DataStage input folder |
| `INFA_FOLDER` | `TEST_DATA\INFO_DATA` | Informatica input folder |
| `OUTPUT_FOLDER` | `OUTPUT` | Output folder |
| `TEMP_FOLDER` | `OUTPUT\_tmp` | DuckDB database, spill files, transcoded copies |
| `DS_SUFFIX` / `INFA_SUFFIX` | `_ds.csv` / `_infa.csv` | File name suffixes, matched case-insensitively |
| `MEMORY_LIMIT_MAX_GB` | `2` | Highest DuckDB memory cap |
| `MEMORY_LIMIT_MIN_GB` | `1` | Lowest DuckDB memory cap |
| `MEMORY_SHARE_OF_AVAILABLE` | `0.5` | Share of currently available memory given to DuckDB |
| `THREADS` | `None` (= `min(4, CPU cores)`) | DuckDB threads |
| `READ_CHUNK_MB` | `16` | Chunk size for the encoding check and transcoding |
| `SNIFF_SAMPLE_ROWS` | `20480` | Rows sampled to detect the CSV format |
| `FORMAT_OVERRIDES` | `{}` | Per prefix and side, for example `{"customer": {"infa": {"delim": "\|", "quote": '"'}}}` |
| `MAX_LINE_SIZE_BYTES` | `2097152` | Longest allowed line; longer lines are rejected |
| `REJECT_LIMIT_PERCENT` | `1.0` | Stop the pair when more lines than this are malformed |
| `KEY_MODE` | `"auto"` | `"auto"` or `"none"`, for prefixes not in `KEY_OVERRIDES` |
| `KEY_OVERRIDES` | `{}` | Per prefix: a list of key columns, or `None` for full-row output |
| `KEY_MIN_OVERLAP` | `0.80` | Minimum overlap for a key candidate |
| `KEY_MAX_COLUMNS` | `3` | Largest key combination tried automatically |
| `KEY_CANDIDATE_LIMIT` | `5` | Candidates used to build combinations |
| `KEY_NAME_HINTS` | `["ID", "KEY", "NO", "NUM", "CODE"]` | Header words preferred when several single columns qualify |
| `EXAMPLE_VALUES` | `3` | Examples per column on the Column differences sheet |
| `EXCEL_SAMPLE_ROWS` | `100000` | Data rows per sample sheet, at most 1,048,575 |
| `EXCEL_QUOTE_VALUES` | `True` | Show values in single quotes on the Differences and Column differences sheets |
| `FETCH_BATCH_ROWS` | `10000` | Rows fetched from DuckDB at a time for Excel |
| `DISK_FREE_FACTOR` | `4` | Required free space as a multiple of the pair's combined file size |
| `KEEP_TEMP_ON_ERROR` | `False` | Keep temporary files of a failed pair for inspection |

Example key settings:

```python
KEY_MODE = "auto"

KEY_OVERRIDES = {
    "customer": ["CUST_ID"],             # manual single-column key
    "orders":   ["ORDER_ID", "LINE_NO"], # manual multi-column key
    "product":  None,                    # always full-row output
}
```

## 19. Project files

This tool lives in `CSV_comparator_v2`. Both tools in the repository share the `.venv` at the repository root.

| File / folder | Role |
| --- | --- |
| `configs.py` | All settings in section 18 |
| `CSV-CSV.py` | Entry point; calls `comparator.runner.main()` |
| `requirements.txt` | `duckdb==1.5.5`, `xlsxwriter==3.2.9`, `psutil==7.2.2`. The pinned DuckDB version must pass the self-test (section 7.4). |
| `requirements-dev.txt` | `pytest==9.1.1`, `openpyxl==3.1.5` (tests read workbooks with openpyxl) |
| `comparator/` | Package: `settings`, `model`, `runlog`, `files`, `encoding`, `csvformat`, `db`, `selftest`, `rowcompare`, `columns`, `keys`, `differences`, `csvout`, `excel`, `runner` |
| `tests/` | `make_test_data.py` (edge-case pairs and 10M-row generators), `conftest.py`, and pytest modules. `pytest.ini` sets `addopts = -m "not slow"` so the default run skips the large-file tests. |
| `pytest.ini` | Marker `slow` for 10-million-row timing/memory runs |

## 20. Out of scope

- **Normalization.** No trimming, numeric cleanup, case folding, or date reformatting of data values. Differences in spaces, zero padding, and spelling are reported as mismatches. Trimming and case-insensitivity apply only to header names in the column check.
- **Numeric comparison.** `10` and `10.00` are different values. Types are reported, never used to match.
- **Row-number comparison.** Rows are never paired by position.
- **Line numbers for data rows** (section 12.3).
- **Comparing when columns differ.** The pair stops after counting rows.
