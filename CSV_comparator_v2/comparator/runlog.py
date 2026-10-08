"""Console progress and run_log.txt."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path


class RunLog:
    def __init__(self, output_folder: Path) -> None:
        self.output_folder = Path(output_folder)
        self.output_folder.mkdir(parents=True, exist_ok=True)
        self.path = self.output_folder / "run_log.txt"
        self._lines: list[str] = []
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._lines.append(f"=== CSV–CSV comparator run started {stamp} ===")

    def log(self, message: str, *, also_print: bool = True) -> None:
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{stamp}] {message}"
        self._lines.append(line)
        if also_print:
            print(message, flush=True)

    def progress(self, message: str) -> None:
        print(message, flush=True)

    def flush(self) -> None:
        self.path.write_text("\n".join(self._lines) + "\n", encoding="utf-8")

    def close(self) -> None:
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._lines.append(f"=== run finished {stamp} ===")
        self.flush()
