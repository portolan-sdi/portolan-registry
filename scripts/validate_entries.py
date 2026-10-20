#!/usr/bin/env python3
"""Validate changed registry entries in a pull request.

Reads the list of changed files (one path per line) and, for each one that is
a catalog entry, checks the URL shape, validates the submitter address, rejects
duplicates, crawls the catalog, and validates a mirror of its metadata with
rashid and stac-node-validator (registry.validators).

The validators gate a new entry only. An entry is new when the registry has
not published it, has removed it, or published it at another URL. A removed
entry that stays deprecated at its published URL is not new, because the
deprecation does not bring it back (#234). 8 of 13
registered catalogs failed rashid in an August 2026 run, so a change to an
existing entry reports what the validators find and does not fail on it. The
nightly re-validation publishes the same result as
`portolan_registry:stac_valid`.

    uv run --frozen scripts/validate_entries.py --changed-file changed.txt

Pass `--report` to also write the outcome as JSON. The pull request gate runs
on `pull_request`, so on a fork it holds a read-only token and cannot report a
failure itself; it hands this file to the notifier over an artifact instead.
"""

from __future__ import annotations

import argparse
import json
import re
import traceback
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import requests

from registry.contacts import validate_submitter_email
from registry.entries import CATALOG_DIR, deprecation, load_entries, load_entry, normalize_url
from registry.export import EXPORT_PATH, load_links, load_state
from registry.fetch import HttpFetcher
from registry.history import Deletion, deletions, entry_at, url_at
from registry.mirror import MirrorSource
from registry.report import log
from registry.validators import crawl_and_validate

APPROVAL_HINT = (
    "A maintainer with the admin or maintain role must approve the head "
    "commit of this pull request."
)

REMOVAL_FORM = (
    "https://github.com/portolan-sdi/portolan-registry/issues/new?template=catalog-removal.yml"
)

# Matches schema/entry.schema.json.
REASON_MAX = 300

# The filename without its extension is the registry ID. IDs cannot change
# or be reused for another catalog (#242).
STEM_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


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
    To remove a catalog on request, delete its entry file. A maintainer must
    review the request and approve the pull request's head commit.
    """
    return [
        f"{p}: This pull request deletes an entry, which removes the catalog "
        "from the registry. You cannot reuse its ID for another catalog. "
        f"Before deleting the entry, open a catalog removal request: {REMOVAL_FORM} "
        f"{APPROVAL_HINT}"
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


def url_holders(catalog_dir: Path) -> dict[str, set[str]]:
    """Map each normalized URL to the entry files in `catalog_dir` that hold it.

    Keep a set, not one file per URL. A pull request that adds a duplicate
    puts two files on one URL. A map that keeps one of them hides the
    duplicate whenever the new file's name sorts last.
    """
    holders: dict[str, set[str]] = {}
    for cid, entry in load_entries(catalog_dir).items():
        url = entry.get("url")
        if isinstance(url, str) and url:
            holders.setdefault(normalize_url(url), set()).add(f"{cid}.yaml")
    return holders


def _url_of(path: Path) -> str | None:
    """Return the entry's normalized URL, or `None` if it has no URL."""
    url = load_entry(path).get("url")
    return normalize_url(url) if isinstance(url, str) and url else None


def id_errors(
    changed_file: Path,
    added_file: Path,
    *,
    catalog_dir: Path,
    holders: Mapping[str, set[str]],
    base_ref: str | None,
    maintainer_approved: bool,
) -> list[str]:
    """Return an error for each new entry with an invalid registry ID (#242).

    An ID is the filename without its extension and must match `STEM_RE`.
    The other rules read Git history at `base_ref`, the branch the pull
    request merges into. Commits inside the pull request do not register an
    ID, so they cannot retire one.

    - Renames fail even with maintainer approval. The workflow lists paths
      with `--no-renames`, so a rename appears as a deletion and an addition.
      If the added file uses the deleted file's URL at `base_ref`, the change
      is a rename.
    - If the registry deleted an entry at the same path, the new entry fails
      unless a maintainer approves re-registration of the same catalog at the
      same URL.
    - If the registry deleted an entry with the same URL under another ID,
      the new entry fails even with approval. The catalog keeps its old ID.
      The rule stays quiet when another current entry holds the URL, because
      the duplicate URL check reports that. It also stays quiet when the old
      ID's newest deletion held another URL, because no ID is left to name.

    `holders` maps each URL to the current entry files that hold it, from
    `url_holders`. Without `base_ref`, only the `STEM_RE` rule runs.
    """
    added = [p for p in read_paths(added_file) if p.exists()]
    if base_ref is None:
        log("No base ref. Skipping the registry ID history rules.")

    renamed_from: dict[str, Path] = {}
    by_stem: dict[str, Deletion] = {}
    by_url: dict[str, Deletion] = {}
    if base_ref is not None:
        for path in read_paths(changed_file):
            if path.exists():
                continue
            old_url = url_at(base_ref, path)
            if old_url:
                renamed_from[normalize_url(old_url)] = path
        # Newest first, so setdefault keeps the newest deletion of each.
        for deletion in deletions(base_ref, catalog_dir):
            by_stem.setdefault(deletion.stem, deletion)
            if deletion.url:
                by_url.setdefault(normalize_url(deletion.url), deletion)

    errors = []
    for path in added:
        if not STEM_RE.match(path.stem):
            errors.append(
                f"{path}: '{path.stem}' is not a valid registry ID. "
                "Use lowercase letters and digits, with single hyphens between "
                "groups, such as 'city-of-example-parcels'. "
                "You cannot change the ID after the pull request merges."
            )
            continue

        url = _url_of(path)
        old = renamed_from.get(url) if url else None
        if old is not None:
            errors.append(
                f"{path}: This pull request renames '{old.stem}' to '{path.stem}'. "
                f"Registry IDs cannot change. Keep {old.name}. "
                "To change the display title, update the title in your catalog."
            )
            continue

        gone = by_stem.get(path.stem)
        if gone is not None:
            if url and gone.url and normalize_url(gone.url) == url:
                if maintainer_approved:
                    log(
                        f"{path}: A maintainer approved re-registration of "
                        f"'{path.stem}' at the same URL."
                    )
                    continue
                errors.append(
                    f"{path}: Commit {gone.commit[:12]} removed registry ID "
                    f"'{path.stem}'. This entry uses the same URL to register "
                    f"the same catalog again. {APPROVAL_HINT}"
                )
                continue
            errors.append(
                f"{path}: Registry ID '{path.stem}' belonged to another catalog, "
                f"which commit {gone.commit[:12]} removed. "
                "You cannot reuse an ID for another catalog. Choose a different filename."
            )
            continue

        owner = by_url.get(url) if url else None
        if (
            url is not None
            and owner is not None
            and by_stem.get(owner.stem) is owner
            and not (catalog_dir / f"{owner.stem}.yaml").exists()
            # The added entry holds its own URL. Ask about the others only.
            and not holders.get(url, set()) - {path.name}
        ):
            errors.append(
                f"{path}: This URL belonged to registry ID '{owner.stem}', which "
                f"commit {owner.commit[:12]} removed. Registry IDs cannot change. "
                f"To register this catalog again, name the file {owner.stem}.yaml."
            )
    return errors


def _successor_errors(
    path: Path,
    successor: object,
    entries: Mapping[str, Mapping[str, Any]],
    state: Mapping[str, Mapping[str, Any]],
) -> list[str]:
    """Return errors for the `superseded_by` value of one deprecated entry."""
    if not isinstance(successor, str) or not STEM_RE.match(successor):
        return [
            f"{path}: 'superseded_by' must be a registry ID, such as 'city-of-example-parcels'."
        ]
    if successor == path.stem:
        return [f"{path}: 'superseded_by' names this entry. Use the ID of the replacement catalog."]
    if successor not in entries:
        return [
            f"{path}: 'superseded_by' names '{successor}', which has no entry file in "
            "catalogs/. Register the replacement first or in the same pull request."
        ]

    # Follow the replacement chain to check for cycles.
    seen = [path.stem]
    current: str | None = successor
    while current is not None:
        if current in seen:
            chain = " -> ".join([*seen, current])
            return [
                f"{path}: 'superseded_by' forms a cycle: {chain}. "
                "Update the replacement IDs so the chain ends at a different catalog."
            ]
        seen.append(current)
        current = deprecation(dict(entries.get(current, {}))).superseded_by

    if deprecation(dict(entries[successor])).deprecated:
        log(f"  Warning: replacement catalog '{successor}' is also deprecated")
    if state.get(successor, {}).get("status") == "removed":
        log(f"  Warning: replacement catalog '{successor}' has status 'removed' in the export")
    return []


def _entry_deprecation_errors(
    path: Path,
    *,
    added: set[Path],
    entries: Mapping[str, Mapping[str, Any]],
    base_ref: str | None,
    state: Mapping[str, Mapping[str, Any]],
) -> list[str]:
    """Check deprecation fields in an added or modified entry."""
    entry = load_entry(path)
    flag = entry.get("deprecated")
    if "deprecated" in entry and not isinstance(flag, bool):
        return [f"{path}: 'deprecated' must be true or false."]
    extras = [k for k in ("superseded_by", "deprecated_reason") if k in entry]
    if extras and flag is not True:
        return [
            f"{path}: Use {' and '.join(repr(k) for k in extras)} only with 'deprecated: true'."
        ]
    if flag is not True:
        return []
    if path.resolve() in added:
        return [
            f"{path}: A new entry cannot be deprecated. Register the catalog first, "
            "then deprecate it in a separate pull request."
        ]

    errors = []
    reason = entry.get("deprecated_reason")
    if "deprecated_reason" in entry and (
        not isinstance(reason, str) or not 1 <= len(reason) <= REASON_MAX
    ):
        errors.append(
            f"{path}: 'deprecated_reason' must be text with 1 to {REASON_MAX} characters."
        )
    if "superseded_by" in entry:
        errors.extend(_successor_errors(path, entry["superseded_by"], entries, state))

    base = entry_at(base_ref, path) if base_ref else None
    if base is not None and not deprecation(base).deprecated:
        old_url, new_url = base.get("url"), entry.get("url")
        if (
            isinstance(old_url, str)
            and isinstance(new_url, str)
            and normalize_url(old_url) != normalize_url(new_url)
        ):
            errors.append(
                f"{path}: This pull request deprecates the entry and changes its URL. "
                "Submit the deprecation and URL change in separate pull requests."
            )
    return errors


def deprecation_errors(
    changed_file: Path,
    added_file: Path | None,
    *,
    catalog_dir: Path,
    base_ref: str | None,
    state: Mapping[str, Mapping[str, Any]],
) -> list[str]:
    """Check deprecation fields and replacement references in changed entries.

    - `superseded_by` and `deprecated_reason` require `deprecated: true`.
    - Replacement IDs must exist in the proposed catalog entries. References
      cannot point to the same entry or form a cycle.
    - New entries cannot be deprecated.
    - Deprecation and URL changes require separate pull requests. This check
      requires `base_ref`.
    - An entry cannot be deleted while another entry names it as a replacement.

    Maintainer approval does not bypass these checks.
    """
    entries = load_entries(catalog_dir)
    added = {p.resolve() for p in read_paths(added_file)} if added_file else set()
    errors: list[str] = []
    for path in changed_entries(changed_file):
        errors.extend(
            _entry_deprecation_errors(
                path, added=added, entries=entries, base_ref=base_ref, state=state
            )
        )

    for path in read_paths(changed_file):
        if path.exists():
            continue
        referrers = sorted(
            cid for cid, entry in entries.items() if entry.get("superseded_by") == path.stem
        )
        if referrers:
            verb = "names" if len(referrers) == 1 else "name"
            errors.append(
                f"{path}: {', '.join(repr(r) for r in referrers)} {verb} '{path.stem}' in "
                "'superseded_by'. Update or remove those references before deleting this entry."
            )
    return errors


def is_new_entry(
    catalog_id: str,
    url: str,
    published: Mapping[str, Mapping[str, Any]],
    *,
    deprecated: bool = False,
) -> bool:
    """True when the validators gate this entry. See the module docstring.

    A removed catalog that an edit brings back counts as new. A deprecated
    one does not come back that way. The publish run keeps a deprecated
    catalog removed until it passes validation. So an edit that keeps a
    removed entry deprecated at its URL is not a re-registration (#234).
    """
    link = published.get(catalog_id)
    if not link:
        return True
    if link.get("portolan_registry:status") == "removed" and not deprecated:
        return True
    href = link.get("href")
    return not href or normalize_url(href) != normalize_url(url)


def check_entry(
    path: Path,
    *,
    existing_urls: Mapping[str, set[str]],
    state: dict[str, dict[str, Any]],
    source_factory: Callable[[], MirrorSource],
    published: Mapping[str, Mapping[str, Any]] | None = None,
) -> list[str]:
    """Validate one entry. Returns a list of error strings.

    `published` holds the previous export's child links by id. Left out, it
    treats every entry as new, so the validators gate it.
    """
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

    others = sorted(existing_urls.get(normalize_url(url), set()) - {path.name})
    if others:
        return [
            f"{path}: Duplicate catalog URL detected. "
            f"This catalog already exists in {', '.join(repr(o) for o in others)}"
        ]

    gated = is_new_entry(path.stem, url, published or {}, deprecated=deprecation(entry).deprecated)

    # Fetch failures do not block edits to an existing catalog at its
    # registered URL, so an offline catalog can still be deprecated.
    try:
        result, report = crawl_and_validate(url, source_factory)
    except requests.exceptions.RequestException as e:
        if not gated:
            log(
                f"  Could not fetch the catalog ({e}). Its registered URL has not changed, so this does not block the pull request."
            )
            return []
        return [f"{path}: Failed to fetch catalog: {e}"]
    except Exception as e:
        if not gated:
            log(
                f"  Could not process the catalog ({e}). Its registered URL has not changed, so this does not block the pull request."
            )
            return []
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

    if report.error:
        log(f"  Validators could not run: {report.error}")
    for group in report.groups:
        log("  " + group.render().replace("\n", "\n  "))
    if report.passed:
        log("  rashid and stac-node-validator found no errors")

    if not gated:
        if not report.passed:
            log(
                "  Note: this catalog is already registered, so these findings "
                "do not fail the check"
            )
        return []

    errors = []
    if report.error:
        errors.append(f"{path}: The validators could not run: {report.error}")
    errors.extend(f"{path}: {group.render()}" for group in report.groups)
    return errors


def collect_errors(
    *,
    changed_file: Path,
    catalog_dir: Path,
    added_file: Path | None = None,
    maintainer_approved: bool = False,
    export_path: Path = EXPORT_PATH,
    base_ref: str | None = None,
) -> list[str]:
    """Validate every changed entry. Returns error strings, and never raises.

    A crash would leave the notifier with no report to read, and the submitter
    with a red check and no explanation. So an unexpected failure becomes an
    error string like any other. The traceback still reaches the run log.

    If you pass `added_file`, a deleted or edited entry fails unless you set
    `maintainer_approved`. The `id_errors` function also checks each new ID.
    Without `added_file`, only deletions require approval. CI always passes it.

    `export_path` decides which entries are new. CI passes the export from
    the base branch. The pull request can change its own copy, and an entry
    it lists there would skip the validators.

    `base_ref` names the branch the pull request merges into. The ID history
    rules in `id_errors` read Git history there. CI passes it.
    """
    try:
        state = load_state(export_path)
        published = load_links(export_path)
        existing_urls = url_holders(catalog_dir)

        errors: list[str] = []
        if maintainer_approved:
            log("A maintainer approved the head commit. Changes to current entries pass.")
        else:
            errors.extend(deleted_entries(changed_file))
            if added_file is not None:
                errors.extend(modified_entries(changed_file, added_file))
        if added_file is not None:
            errors.extend(
                id_errors(
                    changed_file,
                    added_file,
                    catalog_dir=catalog_dir,
                    holders=existing_urls,
                    base_ref=base_ref,
                    maintainer_approved=maintainer_approved,
                )
            )
        errors.extend(
            deprecation_errors(
                changed_file,
                added_file,
                catalog_dir=catalog_dir,
                base_ref=base_ref,
                state=state,
            )
        )
        for path in changed_entries(changed_file):
            errors.extend(
                check_entry(
                    path,
                    existing_urls=existing_urls,
                    state=state,
                    source_factory=HttpFetcher,
                    published=published,
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
        "--export",
        default=str(EXPORT_PATH),
        help="The published export that decides which entries are new. "
        "CI passes the copy from the base branch.",
    )
    parser.add_argument(
        "--base-ref",
        help="The Git ref of the branch the pull request merges into, such as "
        "origin/main. The registry ID history rules read it. CI passes it.",
    )
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
        export_path=Path(args.export),
        base_ref=args.base_ref,
    )

    if args.report:
        write_report(Path(args.report), errors)

    if errors:
        log("\n=== ERRORS ===")
        for err in errors:
            nested = err.replace("\n", "\n  ")
            log(f"  - {nested}")
        return 1

    log("\n=== All changed catalogs validated successfully ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
