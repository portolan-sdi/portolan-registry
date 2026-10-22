"""Write the published registry document as a GeoParquet 1.1 file.

The file has one row for each child link. Its columns are the link fields
with the names that the JSON document uses. The geometry column holds the
link bbox as a Polygon, or as a MultiPolygon when the bbox crosses the
antimeridian. stac-geoparquet stores STAC Items only, and the registry
publishes child links, so this file is plain GeoParquet.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import shapely

from registry.bbox import split_antimeridian
from registry.export import PREFIX

GEOPARQUET_VERSION = "1.1.0"
GEOMETRY = "geometry"

_PARTY = pa.struct([("name", pa.string()), ("url", pa.string())])

# The Arrow type of each registry field. The JSON document and the parquet
# file carry the same fields. A test checks that this map and the extension
# schema declare the same set.
FIELD_TYPES: dict[str, pa.DataType] = {
    PREFIX + "id": pa.string(),
    PREFIX + "status": pa.string(),
    PREFIX + "api_type": pa.string(),
    PREFIX + "spec_version": pa.string(),
    PREFIX + "spec_version_mixed": pa.bool_(),
    PREFIX + "stac_version": pa.string(),
    PREFIX + "logo": pa.struct(
        [("href", pa.string()), ("type", pa.string()), ("title", pa.string())]
    ),
    PREFIX + "updated": pa.string(),
    PREFIX + "first_registered": pa.string(),
    PREFIX + "kind": pa.string(),
    PREFIX + "producers": pa.list_(_PARTY),
    PREFIX + "processors": pa.list_(_PARTY),
    PREFIX + "host": _PARTY,
    PREFIX + "licenses": pa.list_(pa.struct([("id", pa.string()), ("count", pa.int64())])),
    PREFIX + "collection_count": pa.int64(),
    PREFIX + "feature_count": pa.int64(),
    PREFIX + "item_count": pa.int64(),
    PREFIX + "asset_count": pa.int64(),
    PREFIX + "total_size_bytes": pa.int64(),
    PREFIX + "counts_partial": pa.bool_(),
    PREFIX + "temporal_extent": pa.list_(pa.string()),
    PREFIX + "last_crawled": pa.string(),
    PREFIX + "last_validated": pa.string(),
    PREFIX + "stale_since": pa.string(),
    PREFIX + "failure_reason": pa.string(),
    PREFIX + "stac_valid": pa.bool_(),
    PREFIX + "stac_invalid_since": pa.string(),
    PREFIX + "has_agents_md": pa.bool_(),
    PREFIX + "has_readme": pa.bool_(),
    PREFIX + "deprecated": pa.bool_(),
    PREFIX + "deprecated_since": pa.string(),
    PREFIX + "deprecated_reason": pa.string(),
    PREFIX + "superseded_by": pa.string(),
    PREFIX + "coverage": pa.string(),
}

# STAC link fields that every row carries.
_LINK_FIELDS: list[tuple[str, pa.DataType]] = [
    ("href", pa.string()),
    ("title", pa.string()),
    ("bbox", pa.list_(pa.float64())),
]


def _geometry(bbox: Sequence[float] | None) -> shapely.Polygon | shapely.MultiPolygon | None:
    if not bbox:
        return None
    boxes = [
        shapely.box(west, south, east, north)
        for west, south, east, north in split_antimeridian(bbox)
    ]
    return boxes[0] if len(boxes) == 1 else shapely.MultiPolygon(boxes)


def build_table(doc: Mapping[str, Any], link_fields: set[str]) -> pa.Table:
    """One row for each child link of `doc`, with the columns `link_fields` names.

    Raises KeyError when `link_fields` names a field that has no Arrow type.
    """
    fields = [*_LINK_FIELDS, *((name, FIELD_TYPES[name]) for name in sorted(link_fields))]
    links = [link for link in doc.get("links", []) if link.get("rel") == "child"]
    geometries = [_geometry(link.get("bbox")) for link in links]
    rows = [{name: link.get(name) for name, _ in fields} for link in links]
    table = pa.Table.from_pylist(rows, schema=pa.schema(fields))
    wkb = [None if g is None else shapely.to_wkb(g, flavor="iso") for g in geometries]
    table = table.append_column(pa.field(GEOMETRY, pa.binary()), pa.array(wkb, pa.binary()))

    present = [g for g in geometries if g is not None]
    column: dict[str, Any] = {
        "encoding": "WKB",
        "geometry_types": sorted({g.geom_type for g in present}),
        # shapely.box writes the exterior ring counterclockwise.
        "orientation": "counterclockwise",
    }
    if present:
        bounds = shapely.total_bounds(present)
        column["bbox"] = [float(v) for v in bounds]
    geo = {"version": GEOPARQUET_VERSION, "primary_column": GEOMETRY, "columns": {GEOMETRY: column}}
    registry = {
        "stac_extensions": doc.get("stac_extensions", []),
        "generated": doc.get(PREFIX + "generated"),
    }
    metadata = {
        b"geo": json.dumps(geo).encode(),
        b"portolan_registry": json.dumps(registry).encode(),
    }
    return table.replace_schema_metadata(metadata)


def write_table(table: pa.Table, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, path, compression="zstd")
