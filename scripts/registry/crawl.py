"""Walk a Portolan catalog and aggregate what the registry publishes.

Structure the crawler assumes, per portolan-spec v0.1.0: the root of every
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

from registry.bbox import collection_bbox, union_bboxes
from registry.fetch import Fetcher, resolve_url
from registry.logo import catalog_logo
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
    bbox: list[float] | None = None
    temporal: list[str | None] | None = None
    license: str | None = None
    spec_version: str | None = None
    row_count: int = 0
    asset_count: int = 0
    size_bytes: int = 0


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
    keywords: list | None
    logo: dict[str, str] | None
    bbox: list[float] | None
    licenses: dict[str, int]
    collection_count: int
    feature_count: int
    item_count: int
    asset_count: int
    total_size_bytes: int
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
        "temporal_extent": None,
        "api_type": None,
        "last_crawled": now.isoformat(),
        "validation": {
            "stac_valid": True,
            "has_versions_json": False,
            "has_portolan_dir": False,
            "has_llms_txt": False,
        },
        "collections": [],
    }


def _summarize_collection(url: str, collection: Mapping) -> CollectionSummary:
    summary = CollectionSummary(
        id=collection.get("id"),
        url=url,
        bbox=collection_bbox(collection),
        license=collection.get("license"),
        spec_version=declared_version(collection),
        row_count=collection.get("table:row_count") or 0,
    )

    extent = collection.get("extent") or {}
    temporal = (extent.get("temporal") or {}).get("interval") or []
    if temporal and temporal[0] and len(temporal[0]) >= 2:
        summary.temporal = list(temporal[0])

    for asset in (collection.get("assets") or {}).values():
        summary.asset_count += 1
        if isinstance(asset, dict) and "file:size" in asset:
            summary.size_bytes += asset["file:size"]

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

    for link in catalog.get("links", []):
        if link.get("rel") == "llms":
            result["validation"]["has_llms_txt"] = True
            break

    # Only the registered catalog has a logo. A sub-catalog's icon belongs to
    # that sub-catalog, and the registry lists neither it nor its branding.
    if is_root:
        result["logo"] = catalog_logo(catalog, catalog_url, fetcher)

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
                result["asset_count"] += summary.asset_count
                result["total_size_bytes"] += summary.size_bytes

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

                for clink in child.get("links", []):
                    if clink.get("rel") == "version-history":
                        result["validation"]["has_versions_json"] = True

            elif child.get("type") == "Catalog":
                sub = crawl_catalog(
                    child_url, fetcher, now=now, seen=seen, versions=versions
                )
                result["collections"].extend(sub["collections"])
                result["collection_count"] += sub["collection_count"]
                result["feature_count"] += sub["feature_count"]
                result["item_count"] += sub["item_count"]
                result["asset_count"] += sub["asset_count"]
                result["total_size_bytes"] += sub["total_size_bytes"]
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
                if sub["validation"]["has_versions_json"]:
                    result["validation"]["has_versions_json"] = True
                if sub["validation"]["has_llms_txt"]:
                    result["validation"]["has_llms_txt"] = True

        except Exception as e:
            log(f"  Warning: Failed to fetch {child_url}: {e}")

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

    result["validation"]["has_portolan_dir"] = fetcher.probe(
        f"{base}/.portolan/config.yaml", method="HEAD", timeout=10
    )

    return result
