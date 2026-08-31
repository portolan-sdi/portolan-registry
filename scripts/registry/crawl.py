"""Walk a Portolan catalog and aggregate what the registry publishes.

Structure the crawler assumes, per portolan-spec v0.1.2: the root of every
catalog is a STAC Catalog at `catalog.json`; catalogs nest arbitrarily deep
via `child` links; collections never nest. Catalogs carry no extent of their
own, so a catalog-level bbox exists only because the registry computes it by
unioning the collections beneath.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, TypedDict
from urllib.parse import urlsplit

from registry.bbox import collection_bbox, union_bboxes
from registry.fetch import Fetcher, resolve_url
from registry.logo import catalog_logo
from registry.provenance import catalog_kind, collection_kind, parties
from registry.report import log

# The Portolan profile defines no fields, so the versioned schema URI in
# `stac_extensions` is the only signal of which specification version an object
# claims (portolan-spec, stac/README.md).
# Three components today, but the URI is the org's to change. Accept any
# dotted form so a future v0.2 reads as a declaration rather than as silence.
PORTOLAN_SCHEMA = re.compile(
    r"^https://schemas\.portolan-sdi\.org/portolan/v(\d+(?:\.\d+)*)/schema\.json$"
)


def declared_version(obj: Mapping) -> str | None:
    """The Portolan specification version an object claims, or None."""
    for uri in obj.get("stac_extensions") or []:
        match = PORTOLAN_SCHEMA.match(uri) if isinstance(uri, str) else None
        if match:
            return match.group(1)
    return None


def _links_to_markdown_file(obj: Mapping, rel: str, filename: str) -> bool:
    """True when `obj` links `filename` under `rel` as Markdown.

    PORTO-CORE-061 and PORTO-CORE-062 each name a relation, a media type, and
    a file. All three must agree. A link that omits `type: text/markdown`
    satisfies neither requirement, and neither does a link that points the
    relation at some other document.

    The file is read from the last path segment, so a catalog may host the
    document anywhere. Query and fragment are dropped first. The relation may
    repeat: a catalog can point `describedby` at a data dictionary next to its
    README, so one conforming link is enough.
    """
    for link in obj.get("links") or []:
        if link.get("rel") != rel or link.get("type") != "text/markdown":
            continue
        href = link.get("href")
        if isinstance(href, str) and urlsplit(href).path.rsplit("/", 1)[-1] == filename:
            return True
    return False


@dataclass
class CollectionSummary:
    """One collection's contribution to its catalog's totals.

    Retained per collection rather than folded straight into scalars. The
    registry is the intended prototype site for a catalog-wide
    stac-geoparquet collection index (portolan-spec#44), which needs
    per-collection extents that the old crawler discarded.
    """

    id: str | None
    url: str
    title: str | None = None
    bbox: list[float] | None = None
    temporal: list[str | None] | None = None
    license: str | None = None
    spec_version: str | None = None
    row_count: int = 0
    asset_count: int = 0
    size_bytes: int = 0
    item_count: int = 0
    # How many of `asset_count` declared `file:size`. A collection can publish
    # assets and no sizes, and the registry has to tell that apart from a
    # collection whose assets really do sum to zero bytes.
    sized_asset_count: int = 0
    # A STAC API that lists its items behind `rel="items"` instead of linking
    # them one by one. The crawler does not page, so this collection's items
    # cannot be counted without a request per page.
    items_unenumerable: bool = False
    # The collection's own `providers`, unmodified. The spec asks every
    # Collection for them and asks nothing of a Catalog, so this is where
    # official-vs-mirror is decided (registry/provenance.py).
    providers: list | None = None


class CrawlResult(TypedDict, total=False):
    """Shape of a crawled catalog.

    A dict rather than a dataclass on purpose: every consumer reads it with
    `.get()`, and the revalidate flow hand-builds a partial result for
    catalogs it could not crawl.
    """

    id: str
    url: str
    title: str | None
    description: str | None
    stac_version: str | None
    spec_version: str | None
    spec_version_mixed: bool
    updated: str | None
    providers: list | None
    # Derived from the providers above and every collection's, per
    # portolan-spec core.md (Source Provenance). Read them off the outermost
    # result: a nested call sees only its own subtree.
    kind: str | None
    producers: list[dict]
    host: dict | None
    keywords: list | None
    logo: dict[str, str] | None
    bbox: list[float] | None
    licenses: dict[str, int]
    collection_count: int
    feature_count: int
    # None when no item was countable: every collection that has items hides
    # them behind a `rel="items"` endpoint. Zero means the catalog was fully
    # enumerable and holds no items.
    item_count: int | None
    asset_count: int
    # None when nothing in the catalog declared `file:size`. Zero would read as
    # a measurement of an empty catalog; this is the absence of a measurement.
    total_size_bytes: int | None
    # True when a count below is a floor rather than a total: a child failed to
    # fetch, or a collection's items could not be enumerated.
    counts_partial: bool
    temporal_extent: list[str | None] | None
    api_type: str | None
    last_crawled: str | None
    validation: dict
    collections: list[CollectionSummary]


def _empty_result(catalog_url: str, catalog: Mapping, now: datetime) -> CrawlResult:
    return {
        "url": catalog_url,
        "title": catalog.get("title"),
        "description": catalog.get("description"),
        "stac_version": catalog.get("stac_version"),
        "spec_version": declared_version(catalog),
        "spec_version_mixed": False,
        # Passed through unparsed. The spec calls this the modification signal
        # (core.md, Mirrors), and the registry has no reason to reformat it.
        "updated": catalog.get("updated"),
        "providers": catalog.get("providers"),
        # Filled in by crawl_catalog, which has the collections to derive from.
        "kind": None,
        "producers": [],
        "host": None,
        "keywords": catalog.get("keywords"),
        # Filled in by crawl_catalog, which has the fetcher needed to check the
        # image is really there.
        "logo": None,
        "bbox": None,
        "licenses": {},
        "collection_count": 0,
        "feature_count": 0,
        "item_count": 0,
        "asset_count": 0,
        "total_size_bytes": 0,
        "counts_partial": False,
        "temporal_extent": None,
        "api_type": None,
        "last_crawled": now.isoformat(),
        # crawl_catalog fills in the two document signals, which it reports
        # for the root of a registered catalog only.
        "validation": {"stac_valid": True},
        "collections": [],
    }


def _summarize_collection(url: str, collection: Mapping) -> CollectionSummary:
    summary = CollectionSummary(
        id=collection.get("id"),
        url=url,
        title=collection.get("title"),
        bbox=collection_bbox(collection),
        license=collection.get("license"),
        spec_version=declared_version(collection),
        row_count=collection.get("table:row_count") or 0,
        providers=collection.get("providers"),
    )

    extent = collection.get("extent") or {}
    temporal = (extent.get("temporal") or {}).get("interval") or []
    if temporal and temporal[0] and len(temporal[0]) >= 2:
        summary.temporal = list(temporal[0])

    for asset in (collection.get("assets") or {}).values():
        summary.asset_count += 1
        if isinstance(asset, dict) and "file:size" in asset:
            summary.sized_asset_count += 1
            summary.size_bytes += asset["file:size"]

    # Items are counted from the links already in hand, never fetched. A
    # catalog's items outnumber its collections by orders of magnitude, so
    # fetching them would multiply the crawl for a number the collection has
    # already told us.
    has_items_endpoint = False
    for link in collection.get("links") or []:
        rel = link.get("rel")
        if rel == "item":
            summary.item_count += 1
        elif rel == "items":
            has_items_endpoint = True
    summary.items_unenumerable = has_items_endpoint and summary.item_count == 0

    return summary


def crawl_catalog(
    catalog_url: str,
    fetcher: Fetcher,
    *,
    now: datetime | None = None,
    seen: set[str] | None = None,
    versions: set[str] | None = None,
) -> CrawlResult:
    """Crawl a catalog and everything beneath it.

    `seen` guards against a sub-catalog that links back to an ancestor. Without
    it such a catalog recurses until RecursionError, having issued thousands of
    requests first. It also means a sub-catalog reachable by two paths counts
    once rather than twice.

    `versions` collects every Portolan version declared anywhere beneath the
    root, so the outermost call can report whether the tree is of one mind. A
    nested call sees only what has been visited so far; read
    `spec_version_mixed` off the outermost result.
    """
    now = now or datetime.now(timezone.utc)
    is_root = seen is None
    seen = seen if seen is not None else set()
    seen.add(catalog_url)
    versions = versions if versions is not None else set()

    log(f"Crawling: {catalog_url}")
    catalog = fetcher.get_json(catalog_url)
    result = _empty_result(catalog_url, catalog, now)
    if result["spec_version"]:
        versions.add(result["spec_version"])

    base = catalog_url.rsplit("/", 1)[0]
    result["api_type"] = "api" if fetcher.probe(f"{base}/search") else "static"

    # Only the registered catalog has a logo. A sub-catalog's icon belongs to
    # that sub-catalog, and the registry lists neither it nor its branding.
    #
    # The two document signals stop at the root for a different reason.
    # PORTO-CORE-061 and PORTO-CORE-062 ask every catalog and collection for
    # AGENTS.md and README.md, and both are MUST. The registry reports what the
    # registered root links and goes no deeper. Whole-tree conformance is
    # rashid's job, and the registry is not a validator. Computing these per
    # sub-catalog and dropping the answer would read as an aggregate that the
    # export never publishes.
    if is_root:
        result["logo"] = catalog_logo(catalog, catalog_url, fetcher)
        result["validation"]["has_agents_md"] = _links_to_markdown_file(
            catalog, "agents", "AGENTS.md"
        )
        result["validation"]["has_readme"] = _links_to_markdown_file(
            catalog, "describedby", "README.md"
        )

    bboxes: list[list[float]] = []
    temporal_extents: list[list[str | None]] = []

    for link in catalog.get("links", []):
        if link.get("rel") != "child":
            continue

        child_url = resolve_url(catalog_url, link["href"])
        if child_url in seen:
            log(f"  Warning: skipping already-visited child {child_url}")
            continue

        try:
            child = fetcher.get_json(child_url)

            if child.get("type") == "Collection":
                seen.add(child_url)
                summary = _summarize_collection(child_url, child)
                result["collections"].append(summary)
                result["collection_count"] += 1
                result["feature_count"] += summary.row_count
                result["item_count"] += summary.item_count
                result["asset_count"] += summary.asset_count
                result["total_size_bytes"] += summary.size_bytes
                if summary.items_unenumerable:
                    result["counts_partial"] = True

                if summary.bbox:
                    bboxes.append(summary.bbox)
                elif (child.get("extent") or {}).get("spatial"):
                    log(f"  Warning: discarding invalid bbox on {child_url}")
                if summary.temporal:
                    temporal_extents.append(summary.temporal)
                if summary.spec_version:
                    versions.add(summary.spec_version)
                    # A collection that declares nothing is a conformance
                    # failure for the validator, not a version disagreement.
                    if (
                        result["spec_version"]
                        and summary.spec_version != result["spec_version"]
                    ):
                        log(
                            f"  Warning: {child_url} declares Portolan "
                            f"{summary.spec_version}, catalog declares "
                            f"{result['spec_version']}"
                        )

            elif child.get("type") == "Catalog":
                sub = crawl_catalog(
                    child_url, fetcher, now=now, seen=seen, versions=versions
                )
                result["collections"].extend(sub["collections"])
                result["collection_count"] += sub["collection_count"]
                result["feature_count"] += sub["feature_count"]
                # A sub-catalog has already resolved its own unmeasurable
                # counts to None. Add what it did measure and let this level
                # decide again, over the whole merged collection list.
                result["item_count"] += sub["item_count"] or 0
                result["asset_count"] += sub["asset_count"]
                result["total_size_bytes"] += sub["total_size_bytes"] or 0
                if sub["counts_partial"]:
                    result["counts_partial"] = True
                if sub["bbox"]:
                    bboxes.append(sub["bbox"])
                if sub["temporal_extent"]:
                    temporal_extents.append(sub["temporal_extent"])
                if (
                    sub["spec_version"]
                    and result["spec_version"]
                    and sub["spec_version"] != result["spec_version"]
                ):
                    log(
                        f"  Warning: {child_url} declares Portolan "
                        f"{sub['spec_version']}, catalog declares "
                        f"{result['spec_version']}"
                    )

        except Exception as e:
            # The crawl keeps going: one unreachable sub-tree should not cost
            # the registry every other catalog. But the counts below are now a
            # floor, and publishing them as totals is what made a failed fetch
            # indistinguishable from a small catalog.
            result["counts_partial"] = True
            log(f"  Warning: Failed to fetch {child_url}: {e}")

    # Decided here, over `result["collections"]`, which already holds every
    # collection merged up from every sub-catalog. A nested catalog therefore
    # keeps a measurement any descendant managed to make, instead of one
    # unmeasurable branch turning the whole tree null.
    if not any(c.sized_asset_count for c in result["collections"]):
        result["total_size_bytes"] = None
    if result["item_count"] == 0 and any(
        c.items_unenumerable for c in result["collections"]
    ):
        result["item_count"] = None

    result["bbox"] = union_bboxes(bboxes)

    # The spec permits a mixed-version catalog and asks a validator to warn
    # rather than reject, so the registry records the disagreement instead of
    # picking a winner.
    result["spec_version_mixed"] = len(versions) > 1

    if temporal_extents:
        starts = [t[0] for t in temporal_extents if t[0]]
        ends = [t[1] for t in temporal_extents if t[1]]
        result["temporal_extent"] = [
            min(starts) if starts else None,
            max(ends) if ends else None,
        ]

    # The whole mix, weighted, rather than one license standing in for the
    # rest. A catalog of 190 ODbL-1.0 collections and 2 CC-BY-4.0 ones has a
    # mix, and any single label for it hides that. Counted from
    # `result["collections"]`, which already holds the collections merged up
    # from every sub-catalog, so a nested tree keeps its licenses instead of
    # collapsing once per level. Sorted by identifier to keep the export diff
    # quiet when nothing moved. Collections declaring no license are absent
    # here; their count is `collection_count` minus the sum of these.
    counts = Counter(c.license for c in result["collections"] if c.license)
    result["licenses"] = dict(sorted(counts.items()))

    # Official or mirror, and the parties the answer rests on. Derived over
    # the merged collection list for the same reason the license mix is, so a
    # nested catalog is read whole rather than one level at a time.
    #
    # The registered root's own `providers` lead, where it declares any. A
    # sub-catalog's are skipped, as its logo and document signals already are:
    # the spec puts this requirement on collections, and the export publishes
    # one answer per registered catalog rather than one per level.
    provider_lists = [result["providers"]] if result["providers"] else []
    provider_lists += [c.providers for c in result["collections"]]
    result["kind"] = catalog_kind(
        collection_kind(providers) for providers in provider_lists
    )
    result["producers"], result["host"] = parties(provider_lists)

    return result
