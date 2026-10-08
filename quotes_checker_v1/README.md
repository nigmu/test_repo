# Quotes checker

This tool answers one question: **which columns in a CSV file are written with quote marks, and which are not?**

A CSV file is a table saved as plain text. The first row is usually the column names. Each later row is one record. A quote mark here means the double quote character `"`, the same character used in `"Pune"`.

## Steps to run

1. Install Python.
2. Name the file so it ends with `.csv`. Example: `customer.csv`.
3. Put the file in `quotes_checker_v1\input_for_quote_check`.
4. Open a command window in the main folder (the one with `requirements.txt`) and create the virtual environment:

```text
python -m venv .venv
```

5. Enter it:

```text
.venv\Scripts\activate
```

6. First time only, install the requirements:

```text
python -m pip install -r requirements.txt
```

7. Run the check:

```text
python -m quotes_checker_v1
```

The short answer appears in the window. The full note for `customer.csv` is `customer.csv.txt` in `quotes_checker_v1\output_for_quote_check`. Later runs start at step 5.

---

## What you see

The black window is the short answer. One block per file:

```text
customer.csv
Columns with "":
  CITY
  NOTE
Columns without "":
  CUST_ID
  AMOUNT
```

- **Columns with ""** — values in that column were wrapped in quotes.
- **Columns without ""** — values in that column were written plain.
- **Columns with both** — some cells in that column have quotes and some do not. This line appears only when a column is mixed.

The text file is the full note for that file. It adds:

- how big the file is, and how long it took to read
- which character separates the columns (comma, pipe, tab, or semicolon)
- which quote mark was used
- a count of quoted cells and plain cells in every column
- a short verdict, such as “every value is quoted” or “mixed”
- a few real examples from the file
- warnings if rows look broken

If you check two or more files in one run, the text report also has a last section, **Across files**, which lines the habits up side by side.

---

## Settings

There is no settings file for this tool. A setting is an extra word you add to the end of the run command. If you add nothing, the tool checks every CSV in the input folder, treats the first row as column names, and reads each file to the end.

Change one thing at a time. Run the command again after each change.

### Read only some files

Leave the input folder as it is, and name the file at the end of the command.

```text
.venv\Scripts\python -m quotes_checker_v1 "quotes_checker_v1\input_for_quote_check\customer.csv"
```

What changes: only `customer.csv` is checked. The other files in the folder are left alone.

You can name more than one file, one after another.

### Look inside subfolders

```text
.venv\Scripts\python -m quotes_checker_v1 -r
```

What changes: CSV files sitting in folders *inside* the input folder are included. Without `-r`, only the files placed directly in `input_for_quote_check` are read.

Example: `input_for_quote_check\march\orders.csv` is skipped in a normal run, and included when you add `-r`.

### The first row is data, not column names

```text
.venv\Scripts\python -m quotes_checker_v1 --no-header
```

What changes: the first row is counted as a record, not as titles. Columns are then called `column 1`, `column 2`, and so on.

Use this when the file has no title row:

```text
101,Pune
102,Mumbai
```

Leave it off for a normal file:

```text
CUST_ID,CITY
101,Pune
```

### The columns are not separated by commas

The tool usually guesses the separator from the file. Tell it yourself when you already know.

Comma (the normal case):

```text
.venv\Scripts\python -m quotes_checker_v1 --delimiter comma
```

Pipe, as in `101|Pune|10.00`:

```text
.venv\Scripts\python -m quotes_checker_v1 --delimiter pipe
```

Tab:

```text
.venv\Scripts\python -m quotes_checker_v1 --delimiter tab
```

Semicolon, common in some European files (`101;Pune;10.00`):

```text
.venv\Scripts\python -m quotes_checker_v1 --delimiter semicolon
```

What changes: the tool splits columns on that character instead of guessing. If the guess was wrong, the column list was wrong too. After you set the right separator, the column names and the quote counts line up with the real table.

If the text report says the separator is only a guess, set `--delimiter` and run the file again.

### The quote mark is not a double quote

Normal files use `"`. A few files use `'`.

```text
.venv\Scripts\python -m quotes_checker_v1 --quote single
```

What changes: `'Pune'` is treated as a quoted value. With the usual double-quote setting, those single quotes would be read as part of the text.

`--quote double` is the normal setting. You only need to type it when you also set other options and want to be explicit.

The separator and the quote mark must be different characters. `--delimiter comma --quote comma` stops at once with an error.

### The file uses a known text encoding

```text
.venv\Scripts\python -m quotes_checker_v1 --encoding utf-8
```

Another common Windows encoding:

```text
.venv\Scripts\python -m quotes_checker_v1 --encoding cp1252
```

What changes: the tool reads the letters using that encoding instead of detecting it. Use this only when names or cities look garbled (for example `Pune` showing up as strange characters). If you are unsure, leave this off and send the text report to the person who supports the tool.

### Read only the start of a large file

```text
.venv\Scripts\python -m quotes_checker_v1 --max-rows 1000
```

What changes: each file stops after 1,000 data rows. The verdict then describes only those rows. Later rows can still be quoted differently, and the text report says the run stopped early.

`--max-rows 0`, or leaving the option off, reads the whole file. That is the right choice for a real answer.

### Putting two settings together

Settings can be combined. This checks every CSV in the input folder, including subfolders, and treats the separator as a pipe:

```text
.venv\Scripts\python -m quotes_checker_v1 -r --delimiter pipe
```

---

## How the check works

The checker reads the file once, from top to bottom. It does not load the whole table into memory, so a file with millions of rows is safe to run. For each cell it only asks: was this value wrapped in quote marks, or written plain?

Those two habits look like this:

```text
CUST_ID,CITY,NOTE
101,Pune,"Pune, MH"
```

- `101` and `Pune` are plain.
- `"Pune, MH"` is quoted. The quotes are there because the city itself contains a comma. Without the quotes, that comma would look like a new column.

Some systems quote every value, even when there is no comma:

```text
"CUST_ID","CITY"
"101","Pune"
```

Here both columns are “with quotes”.

Some systems quote nothing:

```text
CUST_ID,CITY
101,Pune
```

Here both columns are “without quotes”.

An empty cell can also go either way. A blank between commas is an empty plain cell. `""` is an empty quoted cell. The full report counts both.

The screen list is by column, because that is the practical question: “does the customer-id column come with quotes, or not?” The text file then says whether the file follows one habit all the way through, or mixes them.

A quote is part of the file’s writing style. It is not part of the business value. `Pune` and `"Pune"` are the same city. This tool does not compare values between two systems. It only reports how each file was written. The other folder, `CSV_comparator_v2`, is the tool that compares values.
