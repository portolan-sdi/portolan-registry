#!/usr/bin/env python3
"""Build the catalog index of the registry from every registered catalog.

Run from the repository root:

    uv run --frozen --group site scripts/build_catalog_index.py

The script reads the catalog and collection documents of each registered
catalog and writes one GeoParquet file, `exports/collections.parquet`. See
`registry.catalog_index` for the layout.

The script leaves the file untouched when the rows match the previous file,
so a caller can compare the file before and after the run. The script skips
a catalog with the status `removed`. A catalog that does not answer keeps
the rows of the previous file.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

from registry.catalog_index import (
    INDEX_PATH,
    Row,
    Walk,
    build_table,
    check_rows,
    read_index,
    same_rows,
    settle_rows,
    walk_catalog,
    write_index,
)
from registry.entries import CATALOG_DIR, entry_paths, load_entry
from registry.export import EXPORT_PATH, load_state
from registry.fetch import Fetcher, HttpFetcher
from registry.report import log

# The catalogs that the script reads at the same time. Each one uses up to
# WALK_WORKERS threads of its own.
MAX_WORKERS = 4


def walk_entry(path: Path, fetcher_factory: Callable[[], Fetcher]) -> Walk | None:
    """Walk the catalog of one entry file.

    None when the root does not answer, or when the index cannot hold the rows.
    """
    url = load_entry(path).get("url")
    if not url:
        log(f"  {path.stem}: skipping, the entry has no URL")
        return None
    try:
        walk = walk_catalog(path.stem, url, fetcher_factory)
        check_rows(walk.rows)
    except Exception as e:
        log(f"  {path.stem}: FAILED: {e}")
        return None
    note = "" if walk.complete else ", incomplete"
    log(f"  {path.stem}: {len(walk.rows)} row(s){note}")
    return walk


def main(
    argv: list[str] | None = None, *, fetcher_factory: Callable[[], Fetcher] = HttpFetcher
) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--output",
        default=str(INDEX_PATH),
        help="Where to write the index. A file at this path is the previous index.",
    )
    parser.add_argument(
        "--catalog-dir", default=str(CATALOG_DIR), help="Directory of registry entry files."
    )
    parser.add_argument(
        "--export",
        default=str(EXPORT_PATH),
        help="The catalog export, which holds the status of each catalog.",
    )
    args = parser.parse_args(argv)

    output = Path(args.output)
    previous = read_index(output)
    state = load_state(Path(args.export))
    paths = [
        path
        for path in entry_paths(Path(args.catalog_dir))
        if state.get(path.stem, {}).get("status") != "removed"
    ]

    log(f"=== Reading {len(paths)} catalog(s) ===")
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        walks = list(pool.map(lambda path: walk_entry(path, fetcher_factory), paths))

    rows: list[Row] = []
    for path, walk in zip(paths, walks, strict=True):
        rows.extend(settle_rows(path.stem, walk, previous))

    if not rows:
        log("\n=== REFUSING TO WRITE THE INDEX: no catalog gave a row ===")
        return 1

    table = build_table(rows, generated=datetime.now(UTC))
    if same_rows(table, previous):
        log(f"\n=== No change in {table.num_rows} row(s); left {output} untouched ===")
        return 0
    write_index(table, output)
    log(f"\n=== Wrote {output} with {table.num_rows} row(s) from {len(paths)} catalog(s) ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
