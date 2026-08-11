"""Walk a Portolan catalog and aggregate what the registry publishes.

Structure the crawler assumes, per portolan-spec v0.1.0: the root of every
catalog is a STAC Catalog at `catalog.json`; catalogs nest arbitrarily deep
via `child` links; collections never nest. Catalogs carry no extent of their
own, so a catalog-level bbox exists only because the registry computes it by
unioning the collections beneath.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, TypedDict

from registry.bbox import collection_bbox, union_bboxes
from registry.fetch import Fetcher, resolve_url
from registry.report import log


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
    providers: list | None
    keywords: list | None
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
        "providers": catalog.get("providers"),
        "keywords": catalog.get("keywords"),
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
) -> CrawlResult:
    """Crawl a catalog and everything beneath it.

    `seen` guards against a sub-catalog that links back to an ancestor. Without
    it such a catalog recurses until RecursionError, having issued thousands of
    requests first. It also means a sub-catalog reachable by two paths counts
    once rather than twice.
    """
    now = now or datetime.now(timezone.utc)
    seen = seen if seen is not None else set()
    seen.add(catalog_url)

    log(f"Crawling: {catalog_url}")
    catalog = fetcher.get_json(catalog_url)
    result = _empty_result(catalog_url, catalog, now)

    base = catalog_url.rsplit("/", 1)[0]
    result["api_type"] = "api" if fetcher.probe(f"{base}/search") else "static"

    for link in catalog.get("links", []):
        if link.get("rel") == "llms":
            result["validation"]["has_llms_txt"] = True
            break

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

                for clink in child.get("links", []):
                    if clink.get("rel") == "version-history":
                        result["validation"]["has_versions_json"] = True

            elif child.get("type") == "Catalog":
                sub = crawl_catalog(child_url, fetcher, now=now, seen=seen)
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
                if sub["validation"]["has_versions_json"]:
                    result["validation"]["has_versions_json"] = True
                if sub["validation"]["has_llms_txt"]:
                    result["validation"]["has_llms_txt"] = True

        except Exception as e:
            log(f"  Warning: Failed to fetch {child_url}: {e}")

    result["bbox"] = union_bboxes(bboxes)

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
