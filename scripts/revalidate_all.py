#!/usr/bin/env python3
"""Re-crawl every registered catalog and update validation state.

Runs nightly. A catalog that fails goes valid -> stale, and stale -> removed
after 30 days. A catalog that succeeds returns to valid.

    uv run --frozen scripts/revalidate_all.py
    uv run --frozen scripts/revalidate_all.py --no-notify --output -
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from registry.crawl import crawl_catalog
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
from registry.notify import send_stale_notification
from registry.status import update_status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=str(EXPORT_PATH))
    parser.add_argument("--catalog-dir", default=str(CATALOG_DIR))
    parser.add_argument(
        "--no-notify",
        action="store_true",
        help="Never send submitter email. Use for manual and test runs.",
    )
    args = parser.parse_args(argv)

    now = datetime.now(timezone.utc)
    paths = entry_paths(Path(args.catalog_dir))
    state = load_state(EXPORT_PATH)
    previous_links = load_links(EXPORT_PATH)
    fetcher = HttpFetcher()

    newly_stale: list[tuple[str, dict, str]] = []
    crawled: dict[str, dict] = {}

    for path in paths:
        catalog_id = path.stem
        log(f"\n=== Re-validating {catalog_id} ===")
        entry = load_entry(path)

        url = entry.get("url")
        if not url:
            log("  Skipping: Missing URL")
            continue

        current_state = state.get(
            catalog_id,
            {
                "status": "valid",
                "last_validated": None,
                "stale_since": None,
                "failure_reason": None,
            },
        )

        # A removed catalog stays removed until it is re-submitted, which
        # publish handles.
        if current_state.get("status") == "removed":
            log("  Skipping: Already removed")
            continue

        previous_status = current_state.get("status", "valid")

        try:
            result = crawl_catalog(url, fetcher, now=now)
        except Exception as e:
            failure_reason = str(e)
            log(f"  FAILED: {failure_reason}")
            state[catalog_id] = update_status(
                current_state,
                passed=False,
                failure_reason=failure_reason,
                now=now,
            )
            if previous_status == "valid" and state[catalog_id]["status"] == "stale":
                newly_stale.append((catalog_id, entry, failure_reason))
            continue

        result["id"] = catalog_id
        # A shallow clone cannot see the add commit. Keep the date already
        # published rather than moving the catalog's registration to today.
        result["first_registered"] = first_registered(path) or previous_links.get(
            catalog_id, {}
        ).get("portolan_registry:first_registered")
        crawled[catalog_id] = result
        log(f"  OK: {result['title']} ({result['collection_count']} collections)")
        state[catalog_id] = update_status(current_state, passed=True, now=now)

    if newly_stale:
        log(f"\n=== {len(newly_stale)} newly stale catalog(s) ===")
        for catalog_id, entry, failure_reason in newly_stale:
            send_stale_notification(
                catalog_id,
                submitter_email=entry.get("submitter_email"),
                url=entry.get("url", "unknown"),
                failure_reason=failure_reason,
                enabled=not args.no_notify,
            )

    catalogs = []
    for catalog_id, result in crawled.items():
        catalog_state = state.get(catalog_id, {})
        result["status"] = catalog_state.get("status", "valid")
        result["last_validated"] = catalog_state.get("last_validated")
        result["stale_since"] = catalog_state.get("stale_since")
        result["failure_reason"] = catalog_state.get("failure_reason")
        catalogs.append(result)

    # Carry forward catalogs we did not crawl this run, refreshing only their
    # status. Rebuilding them as empty entries would drop their counts and
    # extent, removing them from the registry map for being briefly offline.
    carried = []
    for path in paths:
        catalog_id = path.stem
        if catalog_id in crawled:
            continue
        link = dict(previous_links.get(catalog_id) or {})
        catalog_state = state.get(catalog_id, {})
        if not link:
            entry = load_entry(path)
            link = {
                "rel": "child",
                "href": entry.get("url"),
                "type": "application/json",
                "title": catalog_id,
                "portolan_registry:id": catalog_id,
                "portolan_registry:collection_count": 0,
                "portolan_registry:feature_count": 0,
                # Never crawled and with no previous link to carry, so nothing
                # here was measured. Null and the partial flag say that; a zero
                # would claim an empty catalog.
                "portolan_registry:item_count": None,
                "portolan_registry:total_size_bytes": None,
                "portolan_registry:counts_partial": True,
            }
        link["portolan_registry:status"] = catalog_state.get("status", "removed")
        link["portolan_registry:last_validated"] = catalog_state.get("last_validated")
        link["portolan_registry:stale_since"] = catalog_state.get("stale_since")
        link["portolan_registry:failure_reason"] = catalog_state.get("failure_reason")
        carried.append(link)

    export = build_export(catalogs, now=now, extra_links=carried)

    try:
        check_export_safe(
            export,
            expected_ids={p.stem for p in paths},
            previous_state=load_state(EXPORT_PATH),
        )
    except ExportRefused as e:
        log(f"\n=== REFUSING TO WRITE EXPORT: {e} ===")
        return 1

    output = Path(args.output)
    if args.output == "-":
        json.dump(export, sys.stdout, indent=2)
        sys.stdout.write("\n")
    elif not export_changed(export, output):
        log(f"\n=== No change beyond timestamps; left {args.output} untouched ===")
    else:
        write_export(export, output)
        log(f"\n=== Generated {args.output} with {export['count']} catalog(s) ===")

    counts = {"valid": 0, "stale": 0, "removed": 0}
    for st in state.values():
        counts[st.get("status", "valid")] = counts.get(st.get("status", "valid"), 0) + 1
    log(
        f"\nStatus summary: {counts['valid']} valid, "
        f"{counts['stale']} stale, {counts['removed']} removed"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
