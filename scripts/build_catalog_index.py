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

With `--summary`, the script adds a report in Markdown to that file. With
`--annotate`, it prints a GitHub Actions warning for each catalog with a
problem. The Catalog Index workflow uses both.
"""

from __future__ import annotations

import argparse
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

from registry.catalog_index import (
    INDEX_PATH,
    Row,
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
from registry.index_report import Outcome, summary, warning_commands
from registry.report import log

# The catalogs that the script reads at the same time. Each one uses up to
# WALK_WORKERS threads of its own.
MAX_WORKERS = 4

# The time that the script reads catalogs for. After it, each walk starts no
# fetch, and a catalog with an unread document keeps its previous rows. The
# Catalog Index job stops after 30 minutes and then publishes nothing. A
# whole run took 2 minutes on 13 November 2026.
BUILD_SECONDS = 15 * 60


def walk_entry(
    path: Path, fetcher_factory: Callable[[], Fetcher], deadline: float | None = None
) -> Outcome:
    """Walk the catalog of one entry file.

    The outcome has no walk when the entry file is unreadable, when the root
    does not answer, or when the index cannot hold the rows.
    """
    try:
        url = load_entry(path).get("url")
        if not url:
            log(f"  {path.stem}: skipping, the entry has no URL")
            return Outcome(path.stem, problem="The entry has no URL.", skipped=True)
        walk = walk_catalog(path.stem, url, fetcher_factory, deadline=deadline)
        check_rows(walk.rows)
    except Exception as e:
        log(f"  {path.stem}: FAILED: {e}")
        return Outcome(path.stem, problem=str(e) or type(e).__name__)
    note = "" if walk.complete else ", incomplete"
    log(f"  {path.stem}: {len(walk.rows)} row(s){note}")
    return Outcome(path.stem, walk=walk)


def report(args: argparse.Namespace, outcomes: list[Outcome], headline: str) -> None:
    """Write the job summary and the warnings that the options ask for."""
    if args.annotate:
        for line in warning_commands(outcomes):
            print(line, flush=True)
    if args.summary:
        with open(args.summary, "a", encoding="utf-8") as f:
            f.write(summary(outcomes, headline))


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
    parser.add_argument(
        "--summary", help="Add a report in Markdown to this file, such as $GITHUB_STEP_SUMMARY."
    )
    parser.add_argument(
        "--annotate",
        action="store_true",
        help="Print a GitHub Actions warning for each catalog with a problem.",
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
    deadline = time.monotonic() + BUILD_SECONDS
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        outcomes = list(pool.map(lambda path: walk_entry(path, fetcher_factory, deadline), paths))

    rows: list[Row] = []
    for outcome in outcomes:
        settled = settle_rows(outcome.catalog_id, outcome.walk, previous)
        outcome.kept = len(settled) - (len(outcome.walk.rows) if outcome.walk else 0)
        rows.extend(settled)

    if not rows:
        log("\n=== REFUSING TO WRITE THE INDEX: no catalog gave a row ===")
        report(args, outcomes, "The build wrote no index, because no catalog gave a row.")
        return 1

    table = build_table(rows, generated=datetime.now(UTC))
    if same_rows(table, previous):
        log(f"\n=== No change in {table.num_rows} row(s); left {output} untouched ===")
        report(args, outcomes, f"No row changed. The index keeps its {table.num_rows} rows.")
        return 0
    write_index(table, output)
    log(f"\n=== Wrote {output} with {table.num_rows} row(s) from {len(paths)} catalog(s) ===")
    report(args, outcomes, f"The build wrote {table.num_rows} rows from {len(paths)} catalogs.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
