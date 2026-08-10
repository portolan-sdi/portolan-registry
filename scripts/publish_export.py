#!/usr/bin/env python3
"""Crawl every registered catalog and regenerate exports/catalogs.json.

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
from registry.entries import CATALOG_DIR, entry_paths, load_entry
from registry.export import (
    EXPORT_PATH,
    ExportRefused,
    build_export,
    check_export_safe,
    load_links,
    load_state,
    write_export,
)
from registry.fetch import HttpFetcher
from registry.report import log

MAX_WORKERS = 4


def process_entry(path: Path, now: datetime) -> dict | None:
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

    catalog_dir = Path(args.catalog_dir)
    paths = entry_paths(catalog_dir)
    now = datetime.now(timezone.utc)

    catalogs: list[dict] = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(process_entry, p, now): p for p in paths}
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
    previous_links = load_links(EXPORT_PATH)
    carried = [
        link for cid, link in previous_links.items()
        if cid not in crawled and cid in {p.stem for p in paths}
    ]
    for link in carried:
        log(f"  Carrying forward {link['portolan:id']}: not crawled this run")

    export = build_export(catalogs, now=now, extra_links=carried)

    try:
        check_export_safe(
            export,
            expected_ids={p.stem for p in paths},
            previous_state=previous_state,
        )
    except ExportRefused as e:
        log(f"\n=== REFUSING TO WRITE EXPORT: {e} ===")
        return 1

    if args.output == "-":
        json.dump(export, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        write_export(export, Path(args.output))
        log(f"\n=== Generated {args.output} with {len(catalogs)} catalog(s) ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
