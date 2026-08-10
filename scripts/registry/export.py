"""Building exports/catalogs.json.

The export is itself a STAC Catalog, so STAC Browser can navigate the
registry as a super-catalog of every registered catalog. Each registered
catalog becomes a rel="child" link.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path

from registry.status import parse_timestamp

EXPORT_PATH = Path("exports/catalogs.json")

ROOT_URL = (
    "https://raw.githubusercontent.com/portolan-sdi/portolan-registry"
    "/refs/heads/main/exports/catalogs.json"
)

DESCRIPTION = (
    "A unified registry of Portolan catalogs. Each child link points to a "
    "registered cloud-native STAC catalog."
)


def load_state(export_path: Path = EXPORT_PATH) -> dict[str, dict]:
    """Read per-catalog validation state back out of a previous export.

    The export is the only place this state is stored. Nothing else records
    whether a catalog is stale, since when, or why.
    """
    if not export_path.exists():
        return {}
    with open(export_path) as f:
        existing = json.load(f)

    state: dict[str, dict] = {}
    for link in existing.get("links", []):
        if link.get("rel") != "child" or not link.get("portolan:id"):
            continue
        state[link["portolan:id"]] = {
            "status": link.get("portolan:status", "valid"),
            "last_validated": link.get("portolan:last_validated"),
            "stale_since": link.get("portolan:stale_since"),
            "failure_reason": link.get("portolan:failure_reason"),
        }
    return state


def load_links(export_path: Path = EXPORT_PATH) -> dict[str, dict]:
    """Read the previous child links, keyed by registry id.

    Used to carry a catalog forward unchanged when this run could not crawl
    it. Dropping it instead would erase its counts and extent, taking it off
    the registry map for a transient network failure.
    """
    if not export_path.exists():
        return {}
    with open(export_path) as f:
        existing = json.load(f)
    return {
        link["portolan:id"]: link
        for link in existing.get("links", [])
        if link.get("rel") == "child" and link.get("portolan:id")
    }


def child_link(catalog: Mapping) -> dict:
    """One rel="child" link.

    Registry-only metadata (status, counts, validation flags, crawl
    timestamps) is not present in the child catalogs themselves, so it rides
    inline under the "portolan:" prefix. Spatial extent is the exception: it
    is standard STAC/GeoJSON, so it stays unprefixed as "bbox".
    """
    validation = catalog.get("validation") or {}
    link = {
        "rel": "child",
        "href": catalog["url"],
        "type": "application/json",
        "title": catalog.get("title") or catalog["id"],
    }
    # Omitted rather than nulled when the extent is unknown, so a consumer
    # indexing into it fails on a missing key instead of silently on None.
    if catalog.get("bbox"):
        link["bbox"] = catalog["bbox"]
    link.update(
        {
            "portolan:id": catalog["id"],
            "portolan:status": catalog.get("status", "valid"),
            "portolan:api_type": catalog.get("api_type"),
            "portolan:collection_count": catalog.get("collection_count", 0),
            "portolan:feature_count": catalog.get("feature_count", 0),
            "portolan:item_count": catalog.get("item_count", 0),
            "portolan:asset_count": catalog.get("asset_count", 0),
            "portolan:total_size_bytes": catalog.get("total_size_bytes", 0),
            "portolan:last_crawled": catalog.get("last_crawled"),
            "portolan:last_validated": catalog.get("last_validated"),
            "portolan:stale_since": catalog.get("stale_since"),
            "portolan:failure_reason": catalog.get("failure_reason"),
            "portolan:stac_valid": validation.get("stac_valid", True),
            "portolan:has_versions_json": validation.get("has_versions_json", False),
            "portolan:has_portolan_dir": validation.get("has_portolan_dir", False),
            "portolan:has_llms_txt": validation.get("has_llms_txt", False),
        }
    )
    return link


def build_export(
    catalogs: Sequence[Mapping],
    *,
    now: datetime | None = None,
    extra_links: Sequence[Mapping] = (),
) -> dict:
    """Assemble the full export document.

    `now` is explicit because publish stamps `generated` with the same
    timestamp it wrote to every `last_validated`, while revalidate takes a
    fresh one. `extra_links` carries forward already-built child links for
    catalogs this run could not crawl.
    """
    now = now or datetime.now(timezone.utc)
    links = [
        {
            "rel": "root",
            "href": ROOT_URL,
            "type": "application/json",
            "title": "Portolan Registry",
        },
        {"rel": "self", "href": ROOT_URL, "type": "application/json"},
    ]
    children = [child_link(c) for c in catalogs]
    children.extend(extra_links)
    children.sort(key=lambda link: link["portolan:id"])
    links.extend(children)

    return {
        "type": "Catalog",
        "stac_version": "1.1.0",
        "id": "portolan-registry",
        "title": "Portolan Registry",
        "description": DESCRIPTION,
        "generated": now.isoformat(),
        "count": len(children),
        "links": links,
    }


class ExportRefused(Exception):
    """Raised instead of writing an export that would lose catalogs."""


def check_export_safe(
    export: Mapping,
    *,
    expected_ids: set[str],
    previous_state: Mapping[str, Mapping],
) -> None:
    """Refuse an export that drops registered catalogs or their state.

    The export is the sole store of validation state, and every writer
    regenerates it wholesale. A well-formed but truncated file silently
    destroys stale/removed history with no other copy to restore from, so
    fail the job loudly rather than commit it.
    """
    got = {
        link["portolan:id"]
        for link in export.get("links", [])
        if link.get("rel") == "child" and link.get("portolan:id")
    }

    missing = expected_ids - got
    if missing:
        raise ExportRefused(
            f"export is missing {len(missing)} registered catalog(s): "
            f"{', '.join(sorted(missing))}"
        )

    # Only still-registered catalogs. Deleting an entry file is a deliberate
    # act, and its state should go with it; guarding those too would make
    # removing a catalog impossible.
    dropped_state = {
        cid
        for cid, st in previous_state.items()
        if cid in expected_ids
        and cid not in got
        and any(v is not None for v in st.values())
    }
    if dropped_state:
        raise ExportRefused(
            f"export would discard validation state for: "
            f"{', '.join(sorted(dropped_state))}"
        )


# These three are rewritten on every run, whether or not a catalog moved.
# The nightly ignores them, so a quiet night writes nothing instead of
# committing fresh timestamps and pinging the site cache.
VOLATILE_FIELDS = frozenset(
    {"generated", "portolan:last_crawled", "portolan:last_validated"}
)

# The timestamps are still a freshness signal, so they must not freeze. A
# quiet week ends with one commit that refreshes them. This bounds their lag
# at 7 days and cuts the nightly commits from 30 a month to about 4.
TIMESTAMP_REFRESH_DAYS = 7


def _without_volatile(export: Mapping) -> dict:
    stripped = {k: v for k, v in export.items() if k not in VOLATILE_FIELDS}
    stripped["links"] = [
        {k: v for k, v in link.items() if k not in VOLATILE_FIELDS}
        for link in export.get("links", [])
    ]
    return stripped


def export_changed(
    export: Mapping,
    export_path: Path = EXPORT_PATH,
    *,
    now: datetime | None = None,
    refresh_after: timedelta = timedelta(days=TIMESTAMP_REFRESH_DAYS),
) -> bool:
    """True when the run must write `export` to `export_path`.

    A status transition, a count, an extent, or an added or dropped catalog
    is a change. A new timestamp alone is not, until the timestamps on disk
    are older than `refresh_after`. `now` is injected so tests can age the
    file without waiting a week.
    """
    if not export_path.exists():
        return True
    try:
        with open(export_path) as f:
            previous = json.load(f)
    except (OSError, json.JSONDecodeError):
        # Overwrite an unreadable file. Do not compare against it.
        return True

    if _without_volatile(export) != _without_volatile(previous):
        return True

    now = now or datetime.now(timezone.utc)
    written = parse_timestamp(previous.get("generated"))
    # An export with no readable `generated` has no age to check, so refresh
    # it and give the next run something to measure.
    return written is None or now - written >= refresh_after


def write_export(export: Mapping, export_path: Path = EXPORT_PATH) -> None:
    """Write the export as pretty-printed JSON."""
    export_path.parent.mkdir(parents=True, exist_ok=True)
    with open(export_path, "w") as f:
        json.dump(export, f, indent=2)
