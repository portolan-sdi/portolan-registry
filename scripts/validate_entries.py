#!/usr/bin/env python3
"""Validate changed registry entries in a pull request.

Reads the list of changed files (one path per line) and, for each one that is
a catalog entry, checks the URL shape, validates the submitter address, rejects
duplicates, and crawls the catalog.

    uv run --frozen scripts/validate_entries.py --changed-file changed.txt

Pass `--report` to also write the outcome as JSON. The pull request gate runs
on `pull_request`, so on a fork it holds a read-only token and cannot report a
failure itself; it hands this file to the notifier over an artifact instead.
"""

from __future__ import annotations

import argparse
import json
import traceback
from pathlib import Path

import requests

from registry.contacts import validate_submitter_email
from registry.crawl import crawl_catalog
from registry.entries import CATALOG_DIR, load_entries, load_entry, normalize_url
from registry.export import EXPORT_PATH, load_state
from registry.fetch import HttpFetcher
from registry.report import log


APPROVAL_HINT = (
    "A maintainer with the admin or maintain role must approve the head "
    "commit of this pull request."
)


def read_paths(listing: Path) -> list[Path]:
    """The paths in `listing`, one per line. Blank lines do not count."""
    with open(listing) as f:
        return [Path(line.strip()) for line in f if line.strip()]


def changed_entries(changed_file: Path) -> list[Path]:
    """Existing paths listed in `changed_file`."""
    return [p for p in read_paths(changed_file) if p.exists()]


def deleted_entries(changed_file: Path) -> list[str]:
    """One error for each path in `changed_file` that no longer exists.

    The gate lists every changed entry, deleted ones too. Without this check
    a deletion leaves nothing to validate, and an empty run reports success.
    A catalog leaves the registry through `status: removed` in the export.
    """
    return [
        f"{p}: Deleting an entry is not allowed. Keep the file. A catalog "
        "leaves the registry through 'status: removed' in the export, which "
        f"nightly re-validation sets. {APPROVAL_HINT}"
        for p in read_paths(changed_file)
        if not p.exists()
    ]


def modified_entries(changed_file: Path, added_file: Path) -> list[str]:
    """One error for each current entry that the pull request changes.

    An edit can point another party's catalog at a new URL or address. So
    only a new file passes without review. `added_file` lists the new files.
    """
    added = {p.resolve() for p in read_paths(added_file)}
    return [
        f"{p}: This pull request changes a current entry. {APPROVAL_HINT}"
        for p in changed_entries(changed_file)
        if p.resolve() not in added
    ]


def check_entry(
    path: Path,
    *,
    existing_urls: dict[str, str],
    state: dict[str, dict],
    fetcher: HttpFetcher,
) -> list[str]:
    """Validate one entry. Returns a list of error strings."""
    log(f"\n=== Processing {path} ===")
    entry = load_entry(path)

    url = entry.get("url")
    if not url:
        return [f"{path}: Missing 'url' field"]
    if not url.endswith("catalog.json"):
        return [f"{path}: URL must end with 'catalog.json'"]

    # Checked before the crawl: it costs one DNS lookup rather than a walk of
    # the whole catalog tree, and an entry the registry cannot write back to
    # is rejected whether or not the catalog itself turns out to be sound.
    try:
        submitter = validate_submitter_email(entry.get("submitter_email"), path.stem)
    except ValueError as e:
        return [f"{path}: {e}"]
    log(f"  Submitter: {submitter}")

    catalog_state = state.get(path.stem, {})
    if catalog_state.get("status") == "removed":
        log("  Note: Re-submitting previously removed catalog")
        log(f"  (Was removed due to: {catalog_state.get('failure_reason', 'unknown')})")

    normalized = normalize_url(url)
    for existing_norm, existing_file in existing_urls.items():
        if existing_norm == normalized and existing_file != path.name:
            return [
                f"{path}: Duplicate catalog URL detected. "
                f"This catalog already exists in '{existing_file}'"
            ]

    try:
        result = crawl_catalog(url, fetcher)
    except requests.exceptions.RequestException as e:
        return [f"{path}: Failed to fetch catalog: {e}"]
    except Exception as e:
        return [f"{path}: Error processing catalog: {e}"]

    log(f"  Title: {result['title']}")
    log(f"  Collections: {result['collection_count']}")
    log(f"  Features: {result['feature_count']}")
    items = result["item_count"]
    log(f"  Items: {'not countable' if items is None else items}")
    log(f"  Assets: {result['asset_count']}")
    size = result["total_size_bytes"]
    log(f"  Size: {'no file:size declared' if size is None else f'{size} bytes'}")
    if result["counts_partial"]:
        log("  Warning: counts are a floor; part of this catalog did not enumerate")
    log(f"  Temporal: {result['temporal_extent']}")
    log(f"  API Type: {result['api_type']}")
    log(f"  BBox: {result['bbox']}")
    log(f"  Licenses: {result['licenses']}")
    log(f"  Updated: {result['updated']}")
    log(f"  Portolan version: {result['spec_version'] or 'not declared'}")
    if result["spec_version_mixed"]:
        log("  Warning: this catalog declares more than one Portolan version")
    log(f"  Validation: {result['validation']}")

    return []


def collect_errors(
    *,
    changed_file: Path,
    catalog_dir: Path,
    added_file: Path | None = None,
    maintainer_approved: bool = False,
) -> list[str]:
    """Validate every changed entry. Returns error strings, and never raises.

    A crash would leave the notifier with no report to read, and the submitter
    with a red check and no explanation. So an unexpected failure becomes an
    error string like any other. The traceback still reaches the run log.

    With `added_file`, a deleted or edited entry fails unless
    `maintainer_approved` is set. Without it, only a deletion fails. CI always
    passes `added_file`.
    """
    try:
        state = load_state(EXPORT_PATH)
        existing_urls = {
            normalize_url(entry["url"]): f"{cid}.yaml"
            for cid, entry in load_entries(catalog_dir).items()
            if entry.get("url")
        }

        fetcher = HttpFetcher()
        errors: list[str] = []
        if maintainer_approved:
            log(
                "A maintainer approved the head commit. Changes to current entries pass."
            )
        else:
            errors.extend(deleted_entries(changed_file))
            if added_file is not None:
                errors.extend(modified_entries(changed_file, added_file))
        for path in changed_entries(changed_file):
            errors.extend(
                check_entry(
                    path, existing_urls=existing_urls, state=state, fetcher=fetcher
                )
            )
        return errors
    except Exception as e:
        log(traceback.format_exc())
        return [f"The validation run failed before it could check the entry: {e}"]


def write_report(path: Path, errors: list[str]) -> None:
    """Record the outcome where the notifier can find it."""
    report = {"ok": not errors, "errors": errors}
    path.write_text(json.dumps(report, indent=2) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--changed-file",
        default="changed.txt",
        help="File listing changed paths, one per line.",
    )
    parser.add_argument(
        "--added-file",
        help="File listing the new paths, one per line. "
        "Any other changed entry then needs a maintainer approval.",
    )
    parser.add_argument(
        "--maintainer-approved",
        action="store_true",
        help="A maintainer approved the head commit. Deleted and edited entries pass.",
    )
    parser.add_argument("--catalog-dir", default=str(CATALOG_DIR))
    parser.add_argument(
        "--report",
        help="Write the outcome to this path as JSON. See the module docstring.",
    )
    args = parser.parse_args(argv)

    errors = collect_errors(
        changed_file=Path(args.changed_file),
        catalog_dir=Path(args.catalog_dir),
        added_file=Path(args.added_file) if args.added_file else None,
        maintainer_approved=args.maintainer_approved,
    )

    if args.report:
        write_report(Path(args.report), errors)

    if errors:
        log("\n=== ERRORS ===")
        for err in errors:
            log(f"  - {err}")
        return 1

    log("\n=== All changed catalogs validated successfully ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
