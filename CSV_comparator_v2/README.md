# CSV comparator

This tool compares two extracts of the same data: one from **DataStage**, one from **Informatica**.

For each pair it tells you:

- whether both files have the same columns
- how many rows are exactly the same
- which rows exist on only one side
- when it can, which cells differ, and what each side has

Settings are in `configs.py` in this folder. A normal run does not need any change there.

## Steps to run

1. Install Python.
2. Name the DataStage file`<file_name>_ds.csv` and the Informatica file `<file_name>_infa.csv`. For example `customer_ds.csv` and `customer. A wrong ending, or a file with no partner, is skipped.
3. Put both files in `CSV_comparator_v2/INPUT`.
4. Open a terminal in the main folder (the one with `requirements.txt`) and create the virtual environment:

```text
python3 -m venv .venv
```

1. Enter it:

```text
source .venv/bin/activate
```

1. First time only, install the requirements:

```text
python -m pip install -r requirements.txt
```

1. Run the comparison:

```text
python CSV_comparator_v2/CSV-CSV.py
```

Open `CSV_comparator_v2/OUTPUT/customer_comparison.xlsx`. Close it before running that pair again. Later runs start at step 5.

---

## What you get back

**Open the Excel workbook.** That is the report for a person. The CSV files next to it are the complete lists, which can be very long.

For `customer_ds.csv` and `customer_infa.csv` you may see:


| File                                  | When it appears                                   |
| ------------------------------------- | ------------------------------------------------- |
| `customer_comparison.xlsx`            | Always                                            |
| `customer_comparison_differences.csv` | A key was used, and some cells differ             |
| `customer_comparison_unpaired.csv`    | A key was used, and some rows could not be paired |
| `customer_comparison_mismatches.csv`  | No key was used, and some rows do not match       |
| `customer_comparison_rejected.csv`    | The file had broken lines                         |
| `run_log.txt`                         | Always. One log for the whole run                 |


The workbook can contain these sheets. A sheet is left out when it does not apply.


| Sheet                   | What it tells you                                                                              |
| ----------------------- | ---------------------------------------------------------------------------------------------- |
| Summary                 | The result, row counts, the key that was used, and how long each step took                     |
| Column differences      | For each column, what kind of values it holds, and a few examples that appear on only one side |
| Differences (sample)    | Cell by cell: the column, the Informatica value, the DataStage value                           |
| Unpaired rows (sample)  | Rows that could not be lined up with the other file                                            |
| Mismatches (sample)     | The same idea, used when there is no key. The whole row is shown                               |
| Rejected lines (sample) | Lines the tool could not read as a proper row                                                  |


The sample sheets stop at a row limit (100,000 by default). The CSV file beside the workbook has every row. The Summary sheet says when a sample was cut short, and which CSV has the rest.

**Do not double-click those result CSV files to view them in Excel.** Excel will quietly change what you see: `0000000010.00` can become `10`, and a long number can turn into scientific notation. The file itself is still correct. Use the workbook to read results. If someone must open a result CSV in Excel, use Data → From Text/CSV and set every column to Text.

### The word on the Summary sheet


| Result                              | Meaning                                                                                      |
| ----------------------------------- | -------------------------------------------------------------------------------------------- |
| MATCH                               | The two files contain the same rows, the same number of times. Order does not matter         |
| MISMATCH                            | Something differs. The other sheets say what                                                 |
| COLUMNS DIFFER – comparison stopped | The titles do not line up, so the rows were not compared                                     |
| Too many malformed lines            | Too many broken lines. The format is probably wrong, so the remaining rows were not compared |
| ERROR                               | This pair stopped. The Summary and `run_log.txt` give the reason                             |


---

## How a row is judged the same

A row matches only when every cell is the same, character for character.


| DataStage | Informatica     | Same?                               |
| --------- | --------------- | ----------------------------------- |
| `10.00`   | `10.00`         | Yes                                 |
| `10.00`   | `0000000010.00` | No. The digits differ               |
| `Pune`    | `Pune`          | No. The second has a trailing space |
| `meera`   | `Meera`         | No. Capital letters matter          |
| `10`      | `10.00`         | No                                  |


Blank and `""` are both an empty cell. A cell that contains only a space is not empty.

The order of the rows does not matter. Row 5 in one file can match row 90 in the other.

Copies matter. If DataStage has the same row three times and Informatica has it once, one copy matches and two are extra.

Column titles are more forgiving than the values. `CUST_ID` and `cust_id` are the same title. A space at the end of a title is ignored. The order of columns is not ignored: the third column on one side is compared with the third column on the other.

---

## Settings in `configs.py`

Open `CSV_comparator_v2/configs.py` in a text editor. Do not open it in a word processor.

Each setting is one line: a name, an equals sign, and a value. Change only the value. Keep the quotes you see. `True`, `False`, and `None` must keep that capital letter. Save the file, then run the same command again.

If the black window shows a syntax error before any pair starts, a quote or a comma in `configs.py` was disturbed. Undo that edit and save again.

Most runs should leave this file as it was delivered. The settings below are grouped so the ones you may be asked to change come first.

### File names: `DS_SUFFIX` and `INFA_SUFFIX`

Delivered as:

```text
DS_SUFFIX = "_ds.csv"
INFA_SUFFIX = "_infa.csv"
```

This is the ending the tool looks for. `customer_ds.csv` pairs with `customer_infa.csv`.

Example: the extracts are named `customer_datastage.csv` and `customer_informatica.csv`. Change the two lines to:

```text
DS_SUFFIX = "_datastage.csv"
INFA_SUFFIX = "_informatica.csv"
```

What changes: those endings are now the pair. Files that still end in `_ds.csv` are ignored. The shared name is still the part before the ending, so `customer_datastage.csv` pairs with `customer_informatica.csv`.

### Input and output folders

Delivered as folders inside `CSV_comparator_v2`:

```text
INPUT_FOLDER = _ROOT / "INPUT"
OUTPUT_FOLDER = _ROOT / "OUTPUT"
TEMP_FOLDER = _ROOT / "OUTPUT" / "_tmp"
```

Leave these lines alone if you use the folder in section 2.

Example: the extracts arrive in `/data/extracts`. Replace the input line with:

```text
INPUT_FOLDER = "/data/extracts"
```

Use the full Linux path. Forward slashes are the separators.

What changes: both the `_ds` and `_infa` files are read from that one folder. The output folder stays where it is until you change its line too.

`TEMP_FOLDER` is scratch space used during a run and emptied afterwards. Point it at a fast disk only if a technical person asks you to. Do not point it at a folder you use for something else.

### Which column ties a DataStage row to an Informatica row

This is the setting people change most.

A **key** is the column (or columns) that identify a record, such as customer id. With a key, the report can say “for customer 101, Amount is `10.00` on DataStage and `0000000010.00` on Informatica.” Without a key, the report can only list whole rows that do not exist on the other side.

`KEY_MODE` is the default rule. Delivered as:

```text
KEY_MODE = "auto"
```

`"auto"` means: choose a key when one column (or a small group of columns) looks unique on both sides and mostly contains the same values.

`"none"` means: never choose a key. Every unmatched row is listed in full.

```text
KEY_MODE = "none"
```

What changes: the Differences sheet is not produced. You get a Mismatches sheet of whole rows instead. Use this when you do not trust an automatic id column, for example when the only unique column is a timestamp or an amount.

`KEY_OVERRIDES` beats `KEY_MODE` for the names you list. Delivered as:

```text
KEY_OVERRIDES = {}
```

The `{}` means “no special cases.”

Example: always use `CUST_ID` for the customer files, always use two columns for orders, and never use a key for product.

```text
KEY_OVERRIDES = {
    "customer": ["CUST_ID"],
    "orders": ["ORDER_ID", "LINE_NO"],
    "product": None,
}
```

The name in quotes is the shared file name, without `_ds.csv`. Capital letters do not matter: `"customer"` matches `Customer_DS.csv`. The column name must match a title in the file. Capital letters and spaces at the ends of the title do not matter. `CUST_ID` matches `cust_id`.

What changes:

- `customer` is paired on `CUST_ID`, even if automatic detection would have picked another column.
- `orders` is paired on both `ORDER_ID` and `LINE_NO` together.
- `product` lists whole unmatched rows, because `None` means “no key”.
- Any other pair still follows `KEY_MODE`.

If the column name is wrong, that pair stops with **Invalid key in configs.py** and does not guess a different key. The Summary sheet gives the reason. Fix the name and run again.

A key does not have to be unique when you set it by hand. If the same id appears on two different unmatched rows, those rows are reported as duplicate keys and are not forced together.

### How picky automatic key detection is

These four only matter when `KEY_MODE` is `"auto"` and the file is not listed in `KEY_OVERRIDES`.

```text
KEY_MIN_OVERLAP = 0.80
```

A column can be a key only when at least 80% of its distinct values appear on both sides. `0.80` means 80 percent. `1` would mean “every distinct value must appear on both sides,” which is stricter than you usually want.

Example: ids are written `00101` on one side and `101` on the other. They look different, so the overlap is low and that column is not chosen. That is what you want. Lowering this number, for example to `0.50`, lets a weaker column through and can join the wrong records.

```text
KEY_NAME_HINTS = ["ID", "KEY", "NO", "NUM", "CODE"]
```

When several columns are equally good keys, a title containing one of these words wins. `CUST_ID` beats `LOAD_DATE`.

Example: add account numbers to the preference list.

```text
KEY_NAME_HINTS = ["ID", "KEY", "NO", "NUM", "CODE", "ACCOUNT"]
```

What changes: a column named `ACCOUNT` is preferred over another equally unique column. It does not force a column that fails the overlap or uniqueness checks.

```text
KEY_MAX_COLUMNS = 3
KEY_CANDIDATE_LIMIT = 5
```

If no single column is a good key, the tool tries pairs, then triples, from the 5 strongest candidate columns. It will not try a key made of 4 columns, and it will not try every column in a wide file.

Example: you know the business key is four columns. Set `KEY_MAX_COLUMNS = 4`. Better, name those columns in `KEY_OVERRIDES`, which is exact and does not depend on the search.

Raising `KEY_CANDIDATE_LIMIT` makes the search look at more columns and can take longer. Lowering it can miss a real two-column key.

### Tell the tool the file layout, when the guess is wrong

The tool looks at each file and guesses the separator and the quote mark. A comma file and a pipe file can be compared with each other. You only need an override when that guess is wrong, which usually shows up as **COLUMNS DIFFER** or **Too many malformed lines** on a file you know is valid.

Delivered as:

```text
FORMAT_OVERRIDES = {}
```

Example: the Informatica customer file is separated by `|` and uses double quotes, and you do not want a guess.

```text
FORMAT_OVERRIDES = {
    "customer": {"infa": {"delim": "|", "quote": '"'}},
}
```

`"customer"` must match the shared file name, including capital letters, as written on the DataStage file. `"infa"` is the Informatica file. `"ds"` is the DataStage file. You can set one side, or both.

What changes: that file is read with a pipe separator. A guess is not used for it. Other pairs are still guessed.

A comma file does not need an entry. Leave `FORMAT_OVERRIDES` empty unless a pair misreads.

### What the Excel report shows

```text
EXCEL_QUOTE_VALUES = True
```

On the Differences sheet, and in the example lists, values are shown in single quotes: `'Pune '`. The quote marks are added by the report so a leading or trailing space is visible. They are not in the data.

Set:

```text
EXCEL_QUOTE_VALUES = False
```

What changes: those cells show `Pune` without the extra quotes. A trailing space is then easy to miss. The CSV files never add these quotes, either way.

```text
EXCEL_SAMPLE_ROWS = 100000
```

Each sample sheet keeps at most this many data rows. The ceiling is 1,048,575, because that is Excel’s last row after the title row.

```text
EXCEL_SAMPLE_ROWS = 500
```

What changes: the workbook is smaller and faster to open. You see 500 sample rows. The CSV next to it still has every row. The Summary sheet says the sheet was cut.

```text
EXAMPLE_VALUES = 3
```

The Column differences sheet shows up to 3 example values that appear on only one side.

```text
EXAMPLE_VALUES = 1
```

What changes: one example per side instead of three. Useful when the examples are very long. It does not change which rows are mismatches.

### When to stop because the file looks wrong

```text
REJECT_LIMIT_PERCENT = 1.0
```

A broken line is a row the tool cannot split into the right number of columns, often because a quote was left open. Those lines are counted and listed. They are not compared.

`1.0` means 1 percent. If more than 1 percent of the lines in a file are broken, the pair stops with **Too many malformed lines**. That many failures usually means the separator or the quote mark was wrong, and comparing the rest would be misleading.

```text
REJECT_LIMIT_PERCENT = 5
```

What changes: the pair continues unless more than 5 percent of the lines are broken. Use a higher number only when you know the file really does contain some bad lines and you still want the good rows compared.

```text
MAX_LINE_SIZE_BYTES = 2097152
```

A single line longer than this is refused. `2097152` is 2 megabytes, which is already a very long line. The pair stops with an error rather than skipping that line.

```text
MAX_LINE_SIZE_BYTES = 5000000
```

What changes: lines up to about 5 megabytes are accepted. Set this only when the Summary says a line was too long and you have confirmed the file really has a long line, not a missing line break.

### Disk space and leftover scratch files

```text
DISK_FREE_FACTOR = 4
```

Before a pair starts, the tool checks that the disk has free space of at least 4 times the two files added together. A 1 GB pair wants about 4 GB free. If not, that pair stops with **Not enough disk space** and the next pair still runs.

```text
DISK_FREE_FACTOR = 2
```

What changes: it asks for less free space, so a tight disk can still run. The risk is that a pair with many differences fills the disk halfway through. Leave it at 4 unless the run is stopping and you know the disk has room.

```text
KEEP_TEMP_ON_ERROR = False
```

Scratch files for a pair are deleted when the pair finishes, including when it fails.

```text
KEEP_TEMP_ON_ERROR = True
```

What changes: if a pair fails, its scratch files stay in the temp folder for a technical person to inspect. Successful pairs are still cleaned up. Turn it back to `False` afterwards, or the disk fills up.

### Memory and speed

These protect a PC that is also running other work. The comparison is built so it does not load both files into Excel or into Python. Leave the numbers as delivered unless a run is too slow or stops with an out-of-memory message.

```text
MEMORY_LIMIT_MAX_GB = 2
MEMORY_LIMIT_MIN_GB = 1
MEMORY_SHARE_OF_AVAILABLE = 0.5
```

At the start of each pair the tool looks at free memory, takes half of it (`0.5`), and will not take more than 2 GB or less than 1 GB.

```text
MEMORY_LIMIT_MAX_GB = 4
```

What changes: on a machine with plenty of free memory, a large pair can finish faster. On a busy machine, other programs may slow down. Do not set the minimum higher than the maximum.

```text
THREADS = None
```

`None` means “use up to 4 processors.” More processors are not always faster, because each one also uses memory.

```text
THREADS = 1
```

What changes: the pair uses one processor. It is often slower and uses less memory. If a pair runs out of memory, the tool already retries it once with one processor before reporting an error. You do not need to set this yourself for that retry.

```text
READ_CHUNK_MB = 16
SNIFF_SAMPLE_ROWS = 20480
FETCH_BATCH_ROWS = 10000
```

These are internal chunk sizes.

- `READ_CHUNK_MB` is how much of a file is inspected at a time while checking the text encoding. `16` is 16 megabytes.
- `SNIFF_SAMPLE_ROWS` is how many rows are looked at to guess comma versus pipe. `20480` is about twenty thousand rows.
- `FETCH_BATCH_ROWS` is how many rows are pulled at a time while writing Excel.

Example: the first part of a file is comma-separated, and later rows are not, so the guess is wrong. Raising the sample can help:

```text
SNIFF_SAMPLE_ROWS = 100000
```

What changes: format detection reads further into the file before deciding. It takes a little longer. It does not change the comparison rules. Prefer `FORMAT_OVERRIDES` when you already know the separator.

Leave `READ_CHUNK_MB` and `FETCH_BATCH_ROWS` as they are.

---

## How the comparison works

Think of each file as a pile of index cards. One pile is DataStage. The other is Informatica. The tool does not compare “row 1 with row 1”, because one extra card at the top would make every later card look wrong.

It does four things for each pair.

**1. Can both files be read?**  
It checks the text encoding, finds the separator and the quote mark, and reads the title row. If the titles do not line up, it stops that pair, counts the rows, and writes a Summary only. Comparing values would not mean anything if column 3 is Amount on one side and City on the other.

**2. Which cards are the same card?**  
Two cards match only when every value is identical, including zeros, spaces, and capital letters. The piles can be in any order. Duplicate cards are counted. One hundred matching cards and two extras is a mismatch, not a match.

**3. For the cards that differ, can we tell which record it was?**  
If a key such as customer id is known, or one is found automatically, the tool lines up the unmatched DataStage card with the unmatched Informatica card that has the same id. It then lists the cells that differ: column name, Informatica value, DataStage value. Cards with no partner, and extra copies, are listed separately.

If there is no safe key, it does not invent one. It lists each unmatched card in full.

**4. Write the report and clear the scratch space.**  
The workbook is the short report. The CSV files are the full lists. Scratch files are removed so the next pair starts clean.

A small picture of a real difference:


|                      | DataStage | Informatica     |
| -------------------- | --------- | --------------- |
| Customer 101, Amount | `10.00`   | `0000000010.00` |
| Customer 104, Name   | `meera`   | `Meera`         |


The rows are about the same people, but they are not the same text, so the result is MISMATCH. With `CUST_ID` as the key, the Differences sheet shows Amount for 101 and Name for 104. It does not call `10.00` and `0000000010.00` equal. Deciding whether that difference matters to the business is a person’s job. The tool’s job is to show it, without tidying the values first.

The other folder, `quotes_checker_v1`, answers a different question: whether the columns were written with quote marks. It does not compare DataStage with Informatica.