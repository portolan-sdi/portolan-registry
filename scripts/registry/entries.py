"""Reading the `catalogs/` directory of registry entries.

Each entry is a YAML file whose stem is the registry id. It carries the two
keys a submitter supplies, `url` and `submitter_email`, per
schema/entry.schema.json. An entry can also carry `deprecated: true`, with an
optional `superseded_by` and `deprecated_reason` (#234). Every other field is
crawled from the catalog.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

import yaml

CATALOG_DIR = Path("catalogs")


def entry_paths(catalog_dir: Path = CATALOG_DIR) -> list[Path]:
    """Every registry entry file, in id order."""
    return sorted(catalog_dir.glob("*.yaml"))


def load_entry(path: Path) -> dict[str, Any]:
    """Parse one entry file. Returns {} for an empty file."""
    with open(path) as f:
        return yaml.safe_load(f) or {}


def load_entries(catalog_dir: Path = CATALOG_DIR) -> dict[str, dict[str, Any]]:
    """Map registry id -> entry dict for every file in `catalog_dir`."""
    return {p.stem: load_entry(p) for p in entry_paths(catalog_dir)}


@dataclass(frozen=True)
class Deprecation:
    """What an entry file says about its deprecation.

    `superseded_by` and `reason` are None when the entry gives none, and
    always None when `deprecated` is False.
    """

    deprecated: bool
    superseded_by: str | None = None
    reason: str | None = None


def deprecation(entry: dict[str, Any]) -> Deprecation:
    """Read the deprecation fields of one entry.

    Only the boolean `true` deprecates. The schema and the gate reject the
    other two fields without it, so they are ignored here when it is absent.
    """
    if entry.get("deprecated") is not True:
        return Deprecation(deprecated=False)
    successor = entry.get("superseded_by")
    reason = entry.get("deprecated_reason")
    return Deprecation(
        deprecated=True,
        superseded_by=successor if isinstance(successor, str) and successor else None,
        reason=reason if isinstance(reason, str) and reason else None,
    )


def normalize_url(url: str) -> str:
    """Canonical form of a catalog URL, for duplicate detection.

    Forces https, lowercases the host, drops a leading `www.` and any
    trailing slash, and discards params, query, and fragment.
    """
    parsed = urlparse(url)
    netloc = parsed.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return urlunparse(("https", netloc, parsed.path.rstrip("/"), "", "", ""))
