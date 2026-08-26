"""Collection coverage export behavior."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest

from conftest import FROZEN
from registry.coverage import (
    COVERAGE_PATH,
    build_coverage_export,
    check_coverage_safe,
    coverage_changed,
    coverage_path_for,
    load_coverage,
    write_coverage,
)
from registry.crawl import CollectionSummary
from registry.export import EXPORT_PATH, ExportRefused, ROOT_URL, TIMESTAMP_REFRESH_DAYS


def summary(*, collection_id="collection", title="Collection", bbox=None):
    return CollectionSummary(
        id=collection_id,
        url="https://ex.org/collection.json",
        title=title,
        bbox=bbox,
    )


def catalog(catalog_id="catalog", collections=(), collection_count=None):
    return {
        "id": catalog_id,
        "collection_count": collection_count if collection_count is not None else len(collections),
        "collections": list(collections),
    }


def test_builds_sorted_map_ready_records():
    coverage = build_coverage_export(
        [
            catalog("z", [summary(bbox=[1.23456, 2, 3.45674, 4])]),
            catalog("a", [summary(bbox=[0, 1, 2, 3])]),
        ],
        now=FROZEN,
    )

    assert coverage["generated"] == FROZEN.isoformat()
    assert coverage["registry_generated"] == FROZEN.isoformat()
    assert coverage["source"] == ROOT_URL
    assert [record["id"] for record in coverage["catalogs"]] == ["a", "z"]
    assert coverage["catalogs"][1]["collections"] == [
        {"id": "collection", "title": "Collection", "bbox": [1.2346, 2, 3.4567, 4]}
    ]


def test_uses_url_and_id_as_non_null_fallbacks():
    record = build_coverage_export(
        [catalog(collections=[summary(collection_id=None, title=None, bbox=[0, 0, 1, 1])])],
        now=FROZEN,
    )["catalogs"][0]["collections"][0]
    assert record["id"] == "https://ex.org/collection.json"
    assert record["title"] == record["id"]


def test_keeps_identical_extents_and_omits_invalid_ones():
    coverage = build_coverage_export(
        [
            catalog(
                collections=[
                    summary(collection_id="first", bbox=[0, 0, 1, 1]),
                    summary(collection_id="second", bbox=[0, 0, 1, 1]),
                    summary(collection_id="invalid"),
                ],
                collection_count=3,
            )
        ],
        now=FROZEN,
    )["catalogs"][0]
    assert coverage["collection_count"] == 3
    assert [record["id"] for record in coverage["collections"]] == ["first", "second"]


def test_projects_3d_and_splits_antimeridian_extents():
    records = build_coverage_export(
        [catalog(collections=[summary(bbox=[170, -1, 5, -170, 1, 10])])], now=FROZEN
    )["catalogs"][0]["collections"]
    assert [record["bbox"] for record in records] == [
        [170, -1, 180.0, 1],
        [-180.0, -1, -170, 1],
    ]


def test_carries_catalogs_and_validates_complete_coverage(tmp_path):
    carried = {"id": "a", "collection_count": 4, "collections": []}
    coverage = build_coverage_export(
        [catalog("b")], now=FROZEN, extra_catalogs=[carried]
    )
    assert load_coverage(tmp_path / "missing.json") == {}
    check_coverage_safe(coverage, expected_ids={"a", "b"})
    with pytest.raises(ExportRefused, match="missing 1 registered catalog"):
        check_coverage_safe(coverage, expected_ids={"a", "b", "c"})


def test_change_detection_ignores_fresh_timestamps(tmp_path):
    path = tmp_path / COVERAGE_PATH.name
    coverage = build_coverage_export([catalog()], now=FROZEN)
    write_coverage(coverage, path)
    later = FROZEN + timedelta(days=TIMESTAMP_REFRESH_DAYS - 1)
    assert not coverage_changed(
        build_coverage_export([catalog()], now=later), path, now=later
    )
    changed = build_coverage_export([catalog(collection_count=2)], now=later)
    assert coverage_changed(changed, path, now=later)
    assert coverage_path_for(tmp_path / "catalogs.json") == path


def test_loads_previous_catalog_records(tmp_path):
    path = tmp_path / COVERAGE_PATH.name
    path.write_text(json.dumps({"catalogs": [{"id": "a", "collections": []}]}))
    assert load_coverage(path) == {"a": {"id": "a", "collections": []}}


def test_committed_coverage_matches_the_catalog_export():
    with open(COVERAGE_PATH) as f:
        coverage = json.load(f)
    with open(EXPORT_PATH) as f:
        catalog_export = json.load(f)

    assert coverage["generated"] == catalog_export["generated"]
    assert coverage["registry_generated"] == catalog_export["generated"]
    catalog_ids = {link["portolan_registry:id"] for link in catalog_export["links"] if link.get("rel") == "child"}
    assert {catalog["id"] for catalog in coverage["catalogs"]} == catalog_ids
    for catalog_record in coverage["catalogs"]:
        assert isinstance(catalog_record["collection_count"], int)
        assert isinstance(catalog_record["collections"], list)
        for record in catalog_record["collections"]:
            assert isinstance(record["id"], str) and record["id"]
            assert isinstance(record["title"], str) and record["title"]
            bbox = record["bbox"]
            assert len(bbox) == 4
            assert -180 <= bbox[0] <= 180 and -180 <= bbox[2] <= 180
            assert -90 <= bbox[1] <= 90 and -90 <= bbox[3] <= 90
            assert all(round(value, 4) == value for value in bbox)
