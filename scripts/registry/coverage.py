"""Build exports/coverage-bboxes.json from crawled collection summaries."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path

from registry.crawl import CollectionSummary
from registry.export import ExportRefused, ROOT_URL, TIMESTAMP_REFRESH_DAYS
from registry.status import parse_timestamp

COVERAGE_PATH = Path("exports/coverage-bboxes.json")

_VOLATILE_FIELDS = frozenset({"generated", "registry_generated"})


def coverage_path_for(catalog_export_path: Path) -> Path:
    """Return the coverage export that sits beside a catalog export."""
    return catalog_export_path.with_name(COVERAGE_PATH.name)


def _collection_identity(summary: CollectionSummary) -> tuple[str, str]:
    """Return non-empty values that consumers can display without null checks."""
    collection_id = summary.id if isinstance(summary.id, str) and summary.id else summary.url
    title = summary.title if isinstance(summary.title, str) and summary.title else collection_id
    return collection_id, title


def _map_bboxes(bbox: Sequence[float]) -> list[list[float]]:
    """Convert a cleaned STAC bbox to one or two rounded map rectangles."""
    half = len(bbox) // 2
    west, south, east, north = (round(bbox[i], 4) for i in (0, 1, half, half + 1))
    horizontal = [west, south, east, north]
    if west <= east:
        return [horizontal]
    return [[west, south, 180.0, north], [-180.0, south, east, north]]


def _catalog_coverage(catalog: Mapping) -> dict:
    """Make coverage records for one successfully crawled catalog."""
    records = []
    for summary in catalog.get("collections") or []:
        if not summary.bbox:
            continue
        collection_id, title = _collection_identity(summary)
        for bbox in _map_bboxes(summary.bbox):
            records.append({"id": collection_id, "title": title, "bbox": bbox})
    return {
        "id": catalog["id"],
        "collection_count": catalog.get("collection_count", 0),
        "collections": records,
    }


def build_coverage_export(
    catalogs: Sequence[Mapping],
    *,
    now: datetime | None = None,
    extra_catalogs: Sequence[Mapping] = (),
) -> dict:
    """Assemble the collection coverage export from one registry crawl."""
    now = now or datetime.now(timezone.utc)
    records = [_catalog_coverage(catalog) for catalog in catalogs]
    records.extend(extra_catalogs)
    records.sort(key=lambda catalog: catalog["id"])
    return {
        "generated": now.isoformat(),
        "source": ROOT_URL,
        "registry_generated": now.isoformat(),
        "catalogs": records,
    }


def load_coverage(coverage_path: Path = COVERAGE_PATH) -> dict[str, dict]:
    """Read previous coverage catalog records by registry id."""
    if not coverage_path.exists():
        return {}
    with open(coverage_path) as f:
        coverage = json.load(f)
    return {
        catalog["id"]: catalog
        for catalog in coverage.get("catalogs", [])
        if catalog.get("id")
    }


def check_coverage_safe(coverage: Mapping, *, expected_ids: set[str]) -> None:
    """Refuse a coverage export that would drop a registered catalog."""
    got = {catalog.get("id") for catalog in coverage.get("catalogs", [])}
    missing = expected_ids - got
    if missing:
        raise ExportRefused(
            f"coverage export is missing {len(missing)} registered catalog(s): "
            f"{', '.join(sorted(missing))}"
        )


def _without_volatile(coverage: Mapping) -> dict:
    return {k: v for k, v in coverage.items() if k not in _VOLATILE_FIELDS}


def coverage_changed(
    coverage: Mapping,
    coverage_path: Path = COVERAGE_PATH,
    *,
    now: datetime | None = None,
    refresh_after: timedelta = timedelta(days=TIMESTAMP_REFRESH_DAYS),
) -> bool:
    """True when a coverage export changed beyond its timestamps."""
    if not coverage_path.exists():
        return True
    try:
        with open(coverage_path) as f:
            previous = json.load(f)
    except (OSError, json.JSONDecodeError):
        return True
    if _without_volatile(coverage) != _without_volatile(previous):
        return True
    now = now or datetime.now(timezone.utc)
    written = parse_timestamp(previous.get("generated"))
    return written is None or now - written >= refresh_after


def write_coverage(coverage: Mapping, coverage_path: Path = COVERAGE_PATH) -> None:
    """Write coverage as pretty-printed JSON."""
    coverage_path.parent.mkdir(parents=True, exist_ok=True)
    with open(coverage_path, "w") as f:
        json.dump(coverage, f, indent=2)
