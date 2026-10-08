"""Run the quotes checker from this file's path.

From the repository root, with the shared .venv:

    python -m quotes_checker_v1 data.csv

Or:

    python quotes_checker_v1/check_quotes.py data.csv
"""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    root_text = str(root)
    if root_text not in sys.path:
        sys.path.insert(0, root_text)
    from quotes_checker_v1.__main__ import main as cli

    return cli()


if __name__ == "__main__":
    raise SystemExit(main())
