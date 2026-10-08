"""File discovery and pairing (DESIGN.md section 2)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .settings import Settings


@dataclass(frozen=True)
class FilePair:
    prefix: str
    ds_path: Path
    infa_path: Path


@dataclass
class DiscoveryResult:
    pairs: list[FilePair]
    ignored: list[str]
    unpaired: list[str]


def _normalize_suffix(name: str, suffix: str) -> str | None:
    """Return prefix if name ends with suffix (case-insensitive), else None."""
    lower = name.lower()
    suf = suffix.lower()
    if lower.endswith(suf):
        return name[: -len(suffix)]
    return None


def discover_pairs(settings: Settings) -> DiscoveryResult:
    folder = Path(settings.input_folder)
    ds_suffix = settings.ds_suffix
    infa_suffix = settings.infa_suffix

    ignored: list[str] = []
    ds_by_prefix: dict[str, Path] = {}
    infa_by_prefix: dict[str, Path] = {}

    if not folder.is_dir():
        return DiscoveryResult(
            pairs=[],
            ignored=[f"(missing folder) {folder}"],
            unpaired=[],
        )

    # Longer ending first, so "_infa.csv" is not claimed by a shorter ending.
    rules = sorted(
        (("ds", ds_suffix), ("infa", infa_suffix)),
        key=lambda item: len(item[1]),
        reverse=True,
    )

    for path in sorted(folder.iterdir()):
        if not path.is_file() or path.name.startswith("."):
            continue
        matched = False
        for side, suffix in rules:
            prefix = _normalize_suffix(path.name, suffix)
            if prefix is None:
                continue
            key = prefix.lower()
            if side == "ds":
                ds_by_prefix[key] = path
            else:
                infa_by_prefix[key] = path
            matched = True
            break
        if not matched:
            ignored.append(str(path))

    unpaired: list[str] = []
    pairs: list[FilePair] = []

    all_keys = sorted(set(ds_by_prefix) | set(infa_by_prefix))
    for key in all_keys:
        ds = ds_by_prefix.get(key)
        infa = infa_by_prefix.get(key)
        if ds is None:
            unpaired.append(str(infa))
            continue
        if infa is None:
            unpaired.append(str(ds))
            continue
        # Prefer the casing from the DataStage file name for the display prefix
        prefix = _normalize_suffix(ds.name, ds_suffix) or key
        pairs.append(FilePair(prefix=prefix, ds_path=ds, infa_path=infa))

    pairs.sort(key=lambda p: p.prefix.lower())
    return DiscoveryResult(pairs=pairs, ignored=ignored, unpaired=unpaired)
