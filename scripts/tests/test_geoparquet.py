"""The GeoParquet file of the published registry."""

from __future__ import annotations

import json

import pyarrow.parquet as pq
import shapely

from registry.export import PREFIX
from registry.geoparquet import FIELD_TYPES, build_table, write_table


def doc(*links):
    return {
        "stac_extensions": ["https://ext/v1.json"],
        PREFIX + "generated": "2026-01-15T12:00:00+00:00",
        "links": [{"rel": "self", "href": "x"}, *links],
    }


def link(catalog_id, bbox=None, **fields):
    link = {"rel": "child", "href": f"https://ex.org/{catalog_id}", "title": catalog_id}
    if bbox is not None:
        link["bbox"] = bbox
    return {**link, PREFIX + "id": catalog_id, **fields}


FIELDS = {PREFIX + "id", PREFIX + "licenses"}


def geometries(table):
    return [
        None if g is None else shapely.from_wkb(g) for g in table.column("geometry").to_pylist()
    ]


def test_one_row_per_child_link():
    table = build_table(doc(link("a", [0, 0, 1, 1]), link("b", [2, 2, 3, 3])), FIELDS)
    assert table.num_rows == 2
    assert table.column_names == ["href", "title", "bbox", *sorted(FIELDS), "geometry"]


def test_a_crossing_bbox_becomes_a_multipolygon():
    table = build_table(doc(link("a", [170, -1, -170, 1])), FIELDS)
    [geometry] = geometries(table)
    assert geometry.geom_type == "MultiPolygon"
    assert sorted(p.bounds for p in geometry.geoms) == [(-180, -1, -170, 1), (170, -1, 180, 1)]
    assert table.column("bbox").to_pylist() == [[170, -1, -170, 1]]


def test_a_3d_bbox_becomes_a_2d_polygon():
    [geometry] = geometries(build_table(doc(link("a", [0, 0, 5, 1, 1, 10])), FIELDS))
    assert geometry.bounds == (0, 0, 1, 1)


def test_no_bbox_is_a_null_geometry():
    table = build_table(doc(link("a")), FIELDS)
    assert geometries(table) == [None]
    geo = json.loads(table.schema.metadata[b"geo"])
    assert geo["columns"]["geometry"]["geometry_types"] == []
    assert "bbox" not in geo["columns"]["geometry"]


def test_writes_geoparquet_metadata():
    table = build_table(doc(link("a", [0, 0, 1, 1]), link("b", [170, -1, -170, 1])), FIELDS)
    geo = json.loads(table.schema.metadata[b"geo"])
    assert geo["version"] == "1.1.0"
    assert geo["primary_column"] == "geometry"
    column = geo["columns"]["geometry"]
    assert column["encoding"] == "WKB"
    assert column["geometry_types"] == ["MultiPolygon", "Polygon"]
    assert column["bbox"] == [-180.0, -1.0, 180.0, 1.0]
    assert "crs" not in column


def test_records_the_contract_version():
    table = build_table(doc(link("a", [0, 0, 1, 1])), FIELDS)
    registry = json.loads(table.schema.metadata[b"portolan_registry"])
    assert registry == {
        "stac_extensions": ["https://ext/v1.json"],
        "generated": "2026-01-15T12:00:00+00:00",
    }


def test_keeps_nested_fields_typed(tmp_path):
    licenses = [{"id": "MIT", "count": 2}]
    table = build_table(doc(link("a", [0, 0, 1, 1], **{PREFIX + "licenses": licenses})), FIELDS)
    path = tmp_path / "out" / "catalogs.parquet"
    write_table(table, path)
    read = pq.read_table(path)
    assert read.column(PREFIX + "licenses").to_pylist() == [licenses]
    assert read.schema.field(PREFIX + "licenses").type == FIELD_TYPES[PREFIX + "licenses"]
