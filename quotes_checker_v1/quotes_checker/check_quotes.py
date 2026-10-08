"""Run the quotes checker from this file's path.

From quotes_checker_v1, with the shared repository .venv:

    python -m quotes_checker data.csv

Or, from the repository root:

    python quotes_checker_v1/quotes_checker/check_quotes.py data.csv
"""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    root_text = str(root)
    if root_text not in sys.path:
        sys.path.insert(0, root_text)
    from quotes_checker.__main__ import main as cli

    return cli()


if __name__ == "__main__":
    raise SystemExit(main())
