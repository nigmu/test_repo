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
    ds_folder = Path(settings.ds_folder)
    infa_folder = Path(settings.infa_folder)
    ds_suffix = settings.ds_suffix
    infa_suffix = settings.infa_suffix

    ignored: list[str] = []
    ds_by_prefix: dict[str, Path] = {}
    infa_by_prefix: dict[str, Path] = {}

    if ds_folder.is_dir():
        for path in sorted(ds_folder.iterdir()):
            if not path.is_file():
                continue
            prefix = _normalize_suffix(path.name, ds_suffix)
            if prefix is None:
                ignored.append(str(path))
                continue
            key = prefix.lower()
            ds_by_prefix[key] = path
    else:
        ignored.append(f"(missing folder) {ds_folder}")

    if infa_folder.is_dir():
        for path in sorted(infa_folder.iterdir()):
            if not path.is_file():
                continue
            prefix = _normalize_suffix(path.name, infa_suffix)
            if prefix is None:
                ignored.append(str(path))
                continue
            key = prefix.lower()
            infa_by_prefix[key] = path
    else:
        ignored.append(f"(missing folder) {infa_folder}")

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
