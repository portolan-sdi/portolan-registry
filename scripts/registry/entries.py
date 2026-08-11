"""Reading the `catalogs/` directory of registry entries.

Each entry is a YAML file whose stem is the registry id. It carries the two
keys a submitter supplies, `url` and `submitter_email`, per
schema/entry.schema.json. Every other field is crawled from the catalog.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse, urlunparse

import yaml

CATALOG_DIR = Path("catalogs")


def entry_paths(catalog_dir: Path = CATALOG_DIR) -> list[Path]:
    """Every registry entry file, in id order."""
    return sorted(catalog_dir.glob("*.yaml"))


def load_entry(path: Path) -> dict:
    """Parse one entry file. Returns {} for an empty file."""
    with open(path) as f:
        return yaml.safe_load(f) or {}


def load_entries(catalog_dir: Path = CATALOG_DIR) -> dict[str, dict]:
    """Map registry id -> entry dict for every file in `catalog_dir`."""
    return {p.stem: load_entry(p) for p in entry_paths(catalog_dir)}


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
