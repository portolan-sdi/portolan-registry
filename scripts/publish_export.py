#!/usr/bin/env python3
"""Crawl every registered catalog and regenerate registry exports.

Run from the repository root:

    uv run --frozen scripts/publish_export.py
    uv run --frozen scripts/publish_export.py --output -   # stdout, no write
"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from registry.crawl import crawl_catalog
from registry.coverage import (
    COVERAGE_PATH,
    build_coverage_export,
    check_coverage_safe,
    coverage_changed,
    coverage_path_for,
    load_coverage,
    write_coverage,
)
from registry.entries import CATALOG_DIR, entry_paths, load_entry
from registry.export import (
    EXPORT_PATH,
    ExportRefused,
    build_export,
    check_export_safe,
    export_changed,
    load_links,
    load_state,
    write_export,
)
from registry.fetch import HttpFetcher
from registry.history import first_registered
from registry.report import log

MAX_WORKERS = 4


def process_entry(
    path: Path, now: datetime, previous_links: dict[str, dict]
) -> dict | None:
    """Crawl one registry entry. Returns None if it could not be crawled."""
    log(f"\n=== Processing {path} ===")
    entry = load_entry(path)

    url = entry.get("url")
    if not url:
        log("  Skipping: Missing URL")
        return None

    try:
        # One fetcher per worker: requests makes no thread-safety promise
        # about sharing a Session.
        result = crawl_catalog(url, HttpFetcher(), now=now)
    except Exception as e:
        log(f"  Error: {e}")
        return None

    result["id"] = path.stem
    # A shallow clone cannot see the add commit. Keep the date already
    # published rather than moving the catalog's registration to today.
    result["first_registered"] = first_registered(path) or previous_links.get(
        path.stem, {}
    ).get("portolan_registry:first_registered")
    log(f"  OK: {result['title']} ({result['collection_count']} collections)")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        default=str(EXPORT_PATH),
        help="Where to write the export. '-' writes to stdout without "
        "touching the committed file.",
    )
    parser.add_argument(
        "--catalog-dir",
        default=str(CATALOG_DIR),
        help="Directory of registry entry files.",
    )
    args = parser.parse_args(argv)

    output = Path(args.output)
    if args.output != "-" and output.name == COVERAGE_PATH.name:
        parser.error(
            "--output names the catalog export; it cannot be named "
            f"{COVERAGE_PATH.name}"
        )

    catalog_dir = Path(args.catalog_dir)
    paths = entry_paths(catalog_dir)
    now = datetime.now(timezone.utc)
    expected_ids = {p.stem for p in paths}
    previous_coverage = load_coverage(COVERAGE_PATH)
    previous_links = load_links(EXPORT_PATH)

    catalogs: list[dict] = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(process_entry, p, now, previous_links): p for p in paths
        }
        for future in as_completed(futures):
            result = future.result()
            if result:
                catalogs.append(result)

    catalogs.sort(key=lambda c: c["id"])

    previous_state = load_state(EXPORT_PATH)
    for catalog in catalogs:
        previous = previous_state.get(catalog["id"], {}).get("status")
        if previous in ("stale", "removed"):
            log(f"  Restoring {catalog['id']} from {previous} to valid")
        # A catalog we just crawled is valid by definition. This is what
        # brings a re-submitted catalog back out of removed.
        catalog["status"] = "valid"
        catalog["last_validated"] = now.isoformat()
        catalog["stale_since"] = None
        catalog["failure_reason"] = None

    # Anything we failed to crawl keeps the link it already had, rather than
    # falling out of the export and off the registry map.
    crawled = {c["id"] for c in catalogs}
    carried = [
        link for cid, link in previous_links.items()
        if cid not in crawled and cid in {p.stem for p in paths}
    ]
    for link in carried:
        log(f"  Carrying forward {link['portolan_registry:id']}: not crawled this run")

    export = build_export(catalogs, now=now, extra_links=carried)

    crawled_coverage = {catalog["id"] for catalog in catalogs}
    carried_coverage = [
        previous_coverage[catalog_id]
        for catalog_id in sorted(expected_ids - crawled_coverage)
        if catalog_id in previous_coverage
    ]
    carried_coverage.extend(
        {"id": catalog_id, "collection_count": 0, "collections": []}
        for catalog_id in sorted(expected_ids - crawled_coverage - previous_coverage.keys())
    )
    coverage = build_coverage_export(
        catalogs, now=now, extra_catalogs=carried_coverage
    )

    try:
        check_export_safe(
            export,
            expected_ids=expected_ids,
            previous_state=previous_state,
        )
        check_coverage_safe(coverage, expected_ids=expected_ids)
    except ExportRefused as e:
        log(f"\n=== REFUSING TO WRITE EXPORT: {e} ===")
        return 1

    if args.output == "-":
        json.dump(export, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        coverage_output = coverage_path_for(output)
        changed = export_changed(export, output) or coverage_changed(
            coverage, coverage_output
        )
        if not changed:
            # A manual re-run of an unchanged registry should be a no-op too.
            log("\n=== No change beyond timestamps; left exports untouched ===")
            return 0
        write_export(export, output)
        write_coverage(coverage, coverage_output)
        log(
            f"\n=== Generated {output} and {coverage_output} "
            f"with {len(catalogs)} crawled catalog(s) ==="
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
