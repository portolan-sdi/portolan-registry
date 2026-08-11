"""Crawler traversal and aggregation."""

from __future__ import annotations

import pytest

from conftest import FROZEN, FakeFetcher
from registry.crawl import crawl_catalog

ROOT = "https://ex.org/catalog.json"


def catalog(*children):
    return {
        "type": "Catalog",
        "links": [{"rel": "child", "href": h} for h in children],
    }


def collection(**kw):
    base = {"type": "Collection"}
    base.update(kw)
    return base


class TestAggregation:
    def test_nested_tree_rolls_up_to_the_root(self, tree):
        r = crawl_catalog(ROOT, tree, now=FROZEN)
        assert r["collection_count"] == 3
        assert r["feature_count"] == 1500
        assert r["asset_count"] == 3
        assert r["total_size_bytes"] == 6144
        assert r["bbox"] == [-1.0, 48.0, 7.0, 55.0]
        # Earliest start across collections, latest end.
        assert r["temporal_extent"] == [
            "2019-03-01T00:00:00Z",
            "2024-12-31T00:00:00Z",
        ]

    def test_flags_propagate_from_grandchildren(self, tree):
        r = crawl_catalog(ROOT, tree, now=FROZEN)
        # version-history sits on a collection two levels down.
        assert r["validation"]["has_versions_json"] is True
        # llms link sits on the root.
        assert r["validation"]["has_llms_txt"] is True

    def test_license_mix_is_counted_across_the_whole_tree(self, tree):
        # coastal and alpine are CC-BY-4.0, inland is ODbL-1.0. inland sits
        # under the sub-catalog, and the old single-license roll-up dropped
        # it: each level collapsed to one license before merging upward.
        assert crawl_catalog(ROOT, tree, now=FROZEN)["licenses"] == {
            "CC-BY-4.0": 2,
            "ODbL-1.0": 1,
        }

    def test_unlicensed_collections_are_absent_from_the_mix(self):
        # Not counted under any key, so `collection_count` minus the sum of
        # the counts is how many collections declare nothing.
        f = FakeFetcher(
            docs={
                ROOT: catalog("./a/collection.json", "./b/collection.json"),
                "https://ex.org/a/collection.json": collection(license="CC-BY-4.0"),
                "https://ex.org/b/collection.json": collection(),
            }
        )
        r = crawl_catalog(ROOT, f, now=FROZEN)
        assert r["licenses"] == {"CC-BY-4.0": 1}
        assert r["collection_count"] == 2

    def test_no_licenses_anywhere_is_an_empty_mix(self):
        f = FakeFetcher(
            docs={
                ROOT: catalog("./a/collection.json"),
                "https://ex.org/a/collection.json": collection(),
            }
        )
        assert crawl_catalog(ROOT, f, now=FROZEN)["licenses"] == {}

    def test_per_collection_summaries_are_retained(self, tree):
        r = crawl_catalog(ROOT, tree, now=FROZEN)
        by_id = {c.id: c for c in r["collections"]}
        assert set(by_id) == {"coastal", "inland", "alpine"}
        assert by_id["coastal"].bbox == [2.0, 50.0, 7.0, 55.0]
        assert by_id["alpine"].bbox is None
        assert by_id["inland"].row_count == 300

    def test_item_count_stays_zero(self, tree):
        # Nothing walks rel="item" links; the field is a known placeholder.
        assert crawl_catalog(ROOT, tree, now=FROZEN)["item_count"] == 0


class TestDegenerateCollections:
    @pytest.mark.parametrize(
        "spatial",
        [{}, {"bbox": []}, {"bbox": [[]]}, {"bbox": None}, {"bbox": [None]}],
    )
    def test_bad_spatial_extent_still_counts_the_collection(self, spatial):
        f = FakeFetcher(
            docs={
                ROOT: catalog("./c/collection.json"),
                "https://ex.org/c/collection.json": collection(
                    license="CC-BY-4.0",
                    **{"table:row_count": 17},
                    extent={"spatial": spatial},
                ),
            }
        )
        r = crawl_catalog(ROOT, f, now=FROZEN)
        assert r["collection_count"] == 1
        assert r["feature_count"] == 17
        assert r["licenses"] == {"CC-BY-4.0": 1}
        assert r["bbox"] is None

    def test_empty_temporal_interval_still_counts_the_collection(self):
        f = FakeFetcher(
            docs={
                ROOT: catalog("./c/collection.json"),
                "https://ex.org/c/collection.json": collection(
                    extent={"spatial": {"bbox": [[0, 0, 1, 1]]},
                            "temporal": {"interval": []}},
                ),
            }
        )
        r = crawl_catalog(ROOT, f, now=FROZEN)
        assert r["collection_count"] == 1
        assert r["bbox"] == [0, 0, 1, 1]
        assert r["temporal_extent"] is None

    def test_invalid_bbox_is_dropped_but_collection_counts(self):
        f = FakeFetcher(
            docs={
                ROOT: catalog("./a/collection.json", "./b/collection.json"),
                "https://ex.org/a/collection.json": collection(
                    extent={"spatial": {"bbox": [[-1.79e308, -1.79e308, 5.0, 5.0]]}}
                ),
                "https://ex.org/b/collection.json": collection(
                    extent={"spatial": {"bbox": [[0.0, 0.0, 1.0, 1.0]]}}
                ),
            }
        )
        r = crawl_catalog(ROOT, f, now=FROZEN)
        assert r["collection_count"] == 2
        assert r["bbox"] == [0.0, 0.0, 1.0, 1.0]

    def test_unfetchable_child_is_skipped_not_fatal(self):
        f = FakeFetcher(
            docs={
                ROOT: catalog("./gone/collection.json", "./b/collection.json"),
                "https://ex.org/b/collection.json": collection(
                    extent={"spatial": {"bbox": [[0.0, 0.0, 1.0, 1.0]]}}
                ),
            }
        )
        r = crawl_catalog(ROOT, f, now=FROZEN)
        assert r["collection_count"] == 1

    def test_unfetchable_root_raises(self):
        with pytest.raises(LookupError):
            crawl_catalog(ROOT, FakeFetcher(), now=FROZEN)


class TestCycles:
    def test_subcatalog_linking_back_to_root_terminates(self):
        sub = "https://ex.org/s/catalog.json"
        f = FakeFetcher(
            docs={
                ROOT: catalog("./s/catalog.json"),
                sub: catalog("../catalog.json"),
            }
        )
        r = crawl_catalog(ROOT, f, now=FROZEN)
        assert r["collection_count"] == 0
        assert f.calls.count(ROOT) == 1

    def test_collection_reachable_twice_counts_once(self):
        shared = "https://ex.org/shared/collection.json"
        f = FakeFetcher(
            docs={
                ROOT: catalog("./a/catalog.json", "./b/catalog.json"),
                "https://ex.org/a/catalog.json": catalog("../shared/collection.json"),
                "https://ex.org/b/catalog.json": catalog("../shared/collection.json"),
                shared: collection(
                    **{"table:row_count": 10},
                    extent={"spatial": {"bbox": [[0, 0, 1, 1]]}},
                ),
            }
        )
        r = crawl_catalog(ROOT, f, now=FROZEN)
        assert r["collection_count"] == 1
        assert r["feature_count"] == 10


class TestProbes:
    def test_search_endpoint_marks_catalog_as_api(self):
        f = FakeFetcher(docs={ROOT: catalog()}, ok={"https://ex.org/search"})
        assert crawl_catalog(ROOT, f, now=FROZEN)["api_type"] == "api"

    def test_missing_search_endpoint_marks_static(self):
        f = FakeFetcher(docs={ROOT: catalog()})
        assert crawl_catalog(ROOT, f, now=FROZEN)["api_type"] == "static"

    def test_portolan_config_sets_the_flag(self):
        f = FakeFetcher(
            docs={ROOT: catalog()}, ok={"https://ex.org/.portolan/config.yaml"}
        )
        r = crawl_catalog(ROOT, f, now=FROZEN)
        assert r["validation"]["has_portolan_dir"] is True
