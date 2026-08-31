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


PREFIX = "portolan_registry:"

# The registry once published these fields as bare "portolan:", which reads as
# a claim on the specification's namespace. An export written before the rename
# is still the only copy of its validation state, so accept the old spelling on
# the way in and republish it under the current one.
LEGACY_PREFIX = "portolan:"

# Fields the registry no longer publishes. A carried-forward link is read from
# the previous export and republished as it stands, so a field the crawler
# stopped writing survives in the export until this set drops it. A `removed`
# catalog makes that permanent: revalidate skips it before the crawl, so its
# link is only ever carried, and nothing else can refresh it.
RETIRED_FIELDS = frozenset(
    {
        PREFIX + "has_versions_json",
        PREFIX + "has_portolan_dir",
        PREFIX + "has_llms_txt",
    }
)

# Fields every child link must carry, and what to write when a link predates
# one. A carried link keeps the value it was published with; only a link older
# than the field takes the default. False is the same answer child_link gives
# for a catalog whose crawl recorded no validation.
CARRIED_DEFAULTS: dict[str, object] = {
    PREFIX + "has_agents_md": False,
    PREFIX + "has_readme": False,
    # Null and empty are what child_link writes for a catalog whose providers
    # say nothing, and a link older than these fields is in the same position:
    # the registry has not read the providers it would need.
    PREFIX + "kind": None,
    PREFIX + "producers": [],
    PREFIX + "host": None,
}


def _child_links(export: Mapping) -> list[dict]:
    """Child links from a loaded export, every registry field current.

    Renames, drops, and backfills so a link read here has the field set this
    version of the registry publishes, whichever version wrote it.
    """
    links = []
    for link in export.get("links", []):
        if link.get("rel") != "child":
            continue
        current = {
            (PREFIX + k[len(LEGACY_PREFIX) :] if k.startswith(LEGACY_PREFIX) else k): v
            for k, v in link.items()
        }
        for retired in RETIRED_FIELDS:
            current.pop(retired, None)
        for name, default in CARRIED_DEFAULTS.items():
            current.setdefault(name, default)
        links.append(current)
    return links


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
    for link in _child_links(existing):
        if not link.get("portolan_registry:id"):
            continue
        state[link["portolan_registry:id"]] = {
            "status": link.get("portolan_registry:status", "valid"),
            "last_validated": link.get("portolan_registry:last_validated"),
            "stale_since": link.get("portolan_registry:stale_since"),
            "failure_reason": link.get("portolan_registry:failure_reason"),
        }
    return state


def load_links(export_path: Path = EXPORT_PATH) -> dict[str, dict]:
    """Read the previous child links, keyed by registry id.

    Used to carry a catalog forward unchanged when this run could not crawl
    it. Dropping it instead would erase its counts and extent, taking it off
    the registry map for a transient network failure. A carried link is
    republished as it is read, so it arrives here already renamed.
    """
    if not export_path.exists():
        return {}
    with open(export_path) as f:
        existing = json.load(f)
    return {
        link["portolan_registry:id"]: link
        for link in _child_links(existing)
        if link.get("portolan_registry:id")
    }


def child_link(catalog: Mapping) -> dict:
    """One rel="child" link.

    Everything the registry knows about a catalog rides inline under the
    "portolan_registry:" prefix, including the few values copied from the
    catalog itself, such as `stac_version` and `updated`. STAC defines none of
    them on a link, so an unprefixed name would read as a property of the link
    rather than of the catalog it points at. The prefix names the registry, not
    the standard: none of these fields comes from the Portolan specification,
    and a bare "portolan:" would invite a reader to look for them there.
    Spatial extent is the exception: STAC Browser reads `bbox` off a child link
    to draw the map, so it stays unprefixed.
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
            "portolan_registry:id": catalog["id"],
            "portolan_registry:status": catalog.get("status", "valid"),
            "portolan_registry:api_type": catalog.get("api_type"),
            "portolan_registry:spec_version": catalog.get("spec_version"),
            "portolan_registry:spec_version_mixed": catalog.get(
                "spec_version_mixed", False
            ),
            "portolan_registry:stac_version": catalog.get("stac_version"),
            # {href, type, title} read off the catalog's own icon link, with
            # the href resolved and the image confirmed to exist. Null when
            # the catalog publishes none, which is the common case.
            "portolan_registry:logo": catalog.get("logo"),
            "portolan_registry:updated": catalog.get("updated"),
            "portolan_registry:first_registered": catalog.get("first_registered"),
            # "official", "mirror", or null. Derived from the providers of
            # every collection beneath the catalog, never declared: the
            # specification defines the kind as a reading of the providers and
            # gives it no field of its own (portolan-spec core.md, Source
            # Provenance). Null when no collection names both a producer and a
            # host, which is the registry declining to guess.
            "portolan_registry:kind": catalog.get("kind"),
            # The evidence for the field above, trimmed to {name, url}: who
            # made the data, and who serves this copy. Published so a consumer
            # can show the parties, and re-derive the kind if it reads the
            # providers differently. Empty and null when the catalog names
            # none.
            "portolan_registry:producers": catalog.get("producers") or [],
            "portolan_registry:host": catalog.get("host"),
            # SPDX id -> how many collections declare it. The registry
            # publishes the mix rather than a single label, so a consumer can
            # see a catalog is mostly ODbL-1.0 with two CC-BY-4.0 collections
            # in it. Collections declaring nothing are the difference against
            # portolan_registry:collection_count.
            "portolan_registry:licenses": catalog.get("licenses") or {},
            "portolan_registry:collection_count": catalog.get("collection_count", 0),
            "portolan_registry:feature_count": catalog.get("feature_count", 0),
            # Null, not zero, when the crawl could measure nothing: no item was
            # countable, or no asset declared `file:size`. A zero here would
            # read as a catalog that holds no items or no bytes, which is a
            # different and much stronger claim than "not measured".
            "portolan_registry:item_count": catalog.get("item_count"),
            "portolan_registry:asset_count": catalog.get("asset_count", 0),
            "portolan_registry:total_size_bytes": catalog.get("total_size_bytes"),
            # True when the counts above are a floor: a child failed to fetch,
            # or a collection listed its items behind an endpoint the crawler
            # does not page.
            "portolan_registry:counts_partial": catalog.get("counts_partial", False),
            "portolan_registry:last_crawled": catalog.get("last_crawled"),
            "portolan_registry:last_validated": catalog.get("last_validated"),
            "portolan_registry:stale_since": catalog.get("stale_since"),
            "portolan_registry:failure_reason": catalog.get("failure_reason"),
            "portolan_registry:stac_valid": validation.get("stac_valid", True),
            # Whether the root catalog links the two Markdown documents the
            # specification requires of it, AGENTS.md and README.md.
            "portolan_registry:has_agents_md": validation.get("has_agents_md", False),
            "portolan_registry:has_readme": validation.get("has_readme", False),
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
    children.sort(key=lambda link: link["portolan_registry:id"])
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
        link["portolan_registry:id"]
        for link in export.get("links", [])
        if link.get("rel") == "child" and link.get("portolan_registry:id")
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
    {"generated", "portolan_registry:last_crawled", "portolan_registry:last_validated"}
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
