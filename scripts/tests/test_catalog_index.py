"""The catalog index: one row for each catalog and collection of each tree."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import requests
import shapely
from conftest import FROZEN, FakeFetcher

from registry.catalog_index import (
    INDEX_NAME,
    MEDIA_TYPE,
    REGISTRY_ID,
    SCHEMA,
    Walk,
    build_row,
    build_table,
    check_rows,
    data_link,
    previous_rows,
    read_index,
    same_rows,
    settle_rows,
    walk_catalog,
    write_index,
)

ROOT = "https://ex.org/catalog.json"


def walk(fetcher, catalog_id="example", **kwargs):
    return walk_catalog(catalog_id, ROOT, lambda: fetcher, **kwargs)


def by_id(rows):
    return {row["id"]: row for row in rows}


def collection(collection_id="c", **fields):
    return {"type": "Collection", "id": collection_id, "description": "d", "links": [], **fields}


def catalog(*children, catalog_id="root"):
    links = [{"rel": "child", "href": href} for href in children]
    return {"type": "Catalog", "id": catalog_id, "description": "d", "links": links}


def row_of(doc, url="https://ex.org/c/collection.json"):
    return build_row("example", url, doc, [])


def extent(bbox=None, interval=None):
    return {"spatial": {"bbox": [bbox]}, "temporal": {"interval": [interval]}}


class TestWalk:
    def test_one_row_for_each_catalog_and_collection(self, tree):
        result = walk(tree)
        assert result.complete
        rows = by_id(result.rows)
        assert {row_id: row["type"] for row_id, row in rows.items()} == {
            "example": "Catalog",
            "sub": "Catalog",
            "coastal": "Collection",
            "inland": "Collection",
            "alpine": "Collection",
        }
        assert {row[REGISTRY_ID] for row in result.rows} == {"example"}

    def test_reads_no_item(self, tree):
        walk(tree)
        assert not [call for call in tree.calls if "items" in call]

    def test_parents_run_from_the_root_to_the_direct_parent(self, tree):
        rows = by_id(walk(tree).rows)
        assert rows["example"]["parents"] == []
        assert rows["coastal"]["parents"] == [{"id": "example", "href": ROOT}]
        assert rows["inland"]["parents"] == [
            {"id": "example", "href": ROOT},
            {"id": "sub", "href": "https://ex.org/sub/catalog.json"},
        ]

    def test_each_href_is_absolute(self, tree):
        rows = by_id(walk(tree).rows)
        assert rows["inland"]["href"] == "https://ex.org/sub/inland/collection.json"

    def test_a_missing_child_is_skipped(self, tree):
        del tree.docs["https://ex.org/sub/alpine/collection.json"]
        result = walk(tree)
        assert result.complete
        assert "alpine" not in by_id(result.rows)

    def test_a_child_that_fails_makes_the_walk_incomplete(self, tree):
        tree.docs["https://ex.org/sub/catalog.json"] = requests.ConnectionError("reset")
        result = walk(tree)
        assert not result.complete
        assert set(by_id(result.rows)) == {"example", "coastal"}

    @pytest.mark.parametrize(
        "child",
        [{"type": "Feature", "id": "f"}, {"type": "Collection"}, {"type": "Catalog", "id": 3}, []],
    )
    def test_a_child_that_is_not_a_catalog_or_collection_is_skipped(self, child):
        fetcher = FakeFetcher(docs={ROOT: catalog("./x.json"), "https://ex.org/x.json": child})
        result = walk(fetcher)
        assert result.complete
        assert [row["id"] for row in result.rows] == ["root"]

    def test_a_document_that_two_parents_link_has_one_row(self):
        fetcher = FakeFetcher(
            docs={
                ROOT: catalog("./a/catalog.json", "./b/catalog.json"),
                "https://ex.org/a/catalog.json": catalog("../catalog.json", catalog_id="a"),
                "https://ex.org/b/catalog.json": catalog("../a/catalog.json", catalog_id="b"),
            }
        )
        rows = walk(fetcher).rows
        assert sorted(row["id"] for row in rows) == ["a", "b", "root"]

    def test_a_collection_can_be_a_parent(self):
        """STAC lets a collection hold catalogs. ftw-global-data-2e does it."""
        zone = "https://ex.org/c/zone/catalog.json"
        fetcher = FakeFetcher(
            docs={
                ROOT: catalog("./c/collection.json"),
                "https://ex.org/c/collection.json": collection(
                    links=[{"rel": "child", "href": "./zone/catalog.json"}]
                ),
                zone: catalog(catalog_id="zone"),
            }
        )
        rows = by_id(walk(fetcher).rows)
        assert rows["zone"]["parents"] == [
            {"id": "root", "href": ROOT},
            {"id": "c", "href": "https://ex.org/c/collection.json"},
        ]

    def test_stops_at_the_document_limit(self):
        children = [f"./{n}/collection.json" for n in range(5)]
        docs = {f"https://ex.org/{n}/collection.json": collection(str(n)) for n in range(5)}
        result = walk(FakeFetcher(docs={ROOT: catalog(*children), **docs}), max_documents=3)
        assert not result.complete
        assert len(result.rows) == 3

    def test_a_document_limit_keeps_the_children_already_found(self):
        """The walk reads the children it found before the limit, and no level below."""
        docs = {
            ROOT: catalog("./a/catalog.json", "./b/catalog.json", "./c/catalog.json"),
            "https://ex.org/a/catalog.json": catalog("./deep/catalog.json", catalog_id="a"),
            "https://ex.org/a/deep/catalog.json": catalog(catalog_id="deep"),
            "https://ex.org/b/catalog.json": catalog(catalog_id="b"),
        }
        result = walk(FakeFetcher(docs=docs), max_documents=3)
        assert not result.complete
        assert sorted(row["id"] for row in result.rows) == ["a", "b", "root"]

    def test_stops_at_the_text_limit(self):
        children = [f"./{n}/collection.json" for n in range(5)]
        docs = {f"https://ex.org/{n}/collection.json": collection(str(n)) for n in range(5)}
        fetcher = FakeFetcher(docs={ROOT: catalog(*children), **docs})
        whole = walk(fetcher)
        assert whole.complete and len(whole.rows) == 6
        root_chars = len(whole.rows[0]["original"])
        result = walk(fetcher, max_chars=root_chars + 150)
        assert not result.complete
        assert 1 < len(result.rows) < 6

    def test_raises_when_the_root_does_not_fetch(self):
        with pytest.raises(requests.HTTPError):
            walk(FakeFetcher())

    def test_raises_when_the_root_is_not_a_catalog(self):
        with pytest.raises(ValueError, match="not a STAC catalog or collection"):
            walk(FakeFetcher(docs={ROOT: {"type": "Feature", "id": "f"}}))


class TestRow:
    def test_a_collection_row_copies_the_search_fields(self, tree):
        row = by_id(walk(tree).rows)["coastal"]
        assert row["title"] == "Coastal"
        assert row["description"].startswith("Coastal zones.")
        assert row["license"] == "CC-BY-4.0"
        assert row["bbox"] == {"xmin": 2.0, "ymin": 50.0, "xmax": 7.0, "ymax": 55.0}
        assert shapely.from_wkb(row["geometry"]).bounds == (2.0, 50.0, 7.0, 55.0)
        assert row["start_datetime"] == datetime(2020, 1, 1, tzinfo=UTC)
        assert row["end_datetime"] == datetime(2023, 6, 30, tzinfo=UTC)

    def test_a_catalog_row_has_no_extent_and_no_license(self, tree):
        row = by_id(walk(tree).rows)["example"]
        assert row["title"] == "Example Catalog"
        for name in ("geometry", "bbox", "start_datetime", "end_datetime", "license", "keywords"):
            assert row[name] is None, name

    def test_original_holds_the_full_document(self, tree):
        row = by_id(walk(tree).rows)["coastal"]
        assert json.loads(row["original"]) == tree.docs["https://ex.org/coastal/collection.json"]

    def test_original_keeps_text_outside_ascii(self):
        assert '"Ålesund"' in row_of(collection(title="Ålesund"))["original"]

    def test_links_are_copied_as_the_document_writes_them(self, tree):
        links = by_id(walk(tree).rows)["example"]["links"]
        assert links[0] == {
            "href": "./coastal/collection.json",
            "rel": "child",
            "type": "application/json",
            "title": "Coastal",
        }
        assert {"href": "./AGENTS.md", "rel": "agents", "type": "text/markdown", "title": None} in (
            links
        )

    def test_a_link_without_an_href_is_left_out(self):
        row = row_of(collection(links=[{"rel": "child"}, "text", {"rel": "via", "href": "x"}]))
        assert [link["href"] for link in row["links"]] == ["x"]

    def test_no_bbox_is_a_null_geometry(self, tree):
        row = by_id(walk(tree).rows)["alpine"]
        assert row["geometry"] is None
        assert row["bbox"] is None
        assert row["start_datetime"] is None

    def test_a_crossing_bbox_becomes_a_multipolygon(self):
        row = row_of(collection(extent=extent([170, -1, -170, 1])))
        assert shapely.from_wkb(row["geometry"]).geom_type == "MultiPolygon"
        assert row["bbox"] == {"xmin": 170.0, "ymin": -1.0, "xmax": -170.0, "ymax": 1.0}

    def test_a_3d_bbox_gives_its_2d_values(self):
        row = row_of(collection(extent=extent([0, 1, 5, 2, 3, 10])))
        assert row["bbox"] == {"xmin": 0.0, "ymin": 1.0, "xmax": 2.0, "ymax": 3.0}

    @pytest.mark.parametrize("bad", [[], {"spatial": []}, {"spatial": {"bbox": [7]}}, "text"])
    def test_an_extent_of_another_shape_gives_nulls(self, bad):
        row = row_of(collection(extent=bad))
        assert row["geometry"] is None
        assert row["start_datetime"] is None

    @pytest.mark.parametrize(
        ("interval", "expected"),
        [
            (["2020-01-01T00:00:00Z", None], (datetime(2020, 1, 1, tzinfo=UTC), None)),
            ([None, "2020-01-01T02:00:00+02:00"], (None, datetime(2020, 1, 1, tzinfo=UTC))),
            (["2020-01-01T00:00:00", None], (datetime(2020, 1, 1, tzinfo=UTC), None)),
            (["last year", 2020], (None, None)),
            (["0001-01-01T00:00:00+14:00", None], (None, None)),
            (["2020-01-01T00:00:00Z"], (None, None)),
            (None, (None, None)),
        ],
    )
    def test_reads_the_first_interval_in_utc(self, interval, expected):
        row = row_of(collection(extent=extent([0, 0, 1, 1], interval)))
        assert (row["start_datetime"], row["end_datetime"]) == expected

    def test_keywords_keep_strings_only(self):
        assert row_of(collection(keywords=["a", 1, None, "b"]))["keywords"] == ["a", "b"]
        assert row_of(collection(keywords="a"))["keywords"] is None

    def test_a_field_of_another_type_is_null(self):
        row = row_of(collection(title=["t"], license={"id": "MIT"}, description=None))
        assert (row["title"], row["license"], row["description"]) == (None, None, None)


def table_of(tree, **kwargs):
    return build_table(walk(tree, **kwargs).rows, generated=FROZEN)


class TestTable:
    def test_has_the_columns_of_the_layout(self, tree):
        table = table_of(tree)
        assert table.schema.equals(SCHEMA, check_metadata=False)
        assert table.column_names[:5] == ["type", "id", "href", "links", "parents"]
        assert table.column_names[-1] == "portolan_registry:id"

    def test_writes_geoparquet_metadata_with_the_bbox_covering(self, tree):
        geo = json.loads(table_of(tree).schema.metadata[b"geo"])
        assert geo["version"] == "1.1.0"
        assert geo["primary_column"] == "geometry"
        column = geo["columns"]["geometry"]
        assert column["encoding"] == "WKB"
        assert column["geometry_types"] == ["Polygon"]
        assert column["bbox"] == [-1.0, 48.0, 7.0, 55.0]
        assert column["covering"]["bbox"]["xmin"] == ["bbox", "xmin"]
        assert "crs" not in column

    def test_no_geometry_gives_no_file_bbox(self):
        table = build_table([row_of(collection())], generated=FROZEN)
        column = json.loads(table.schema.metadata[b"geo"])["columns"]["geometry"]
        assert column["geometry_types"] == []
        assert "bbox" not in column

    def test_records_when_the_registry_wrote_it(self, tree):
        registry = json.loads(table_of(tree).schema.metadata[b"portolan_registry"])
        assert registry == {"generated": "2026-01-15T12:00:00+00:00"}

    def test_rows_without_a_bbox_come_first(self, tree):
        """Catalog rows, and alpine, which declares no bbox. They sort by `href`."""
        table = table_of(tree)
        assert table.column("id").to_pylist()[:3] == ["example", "alpine", "sub"]
        assert table.column("bbox").to_pylist()[:3] == [None, None, None]

    def test_the_row_order_does_not_depend_on_the_input_order(self, tree):
        rows = walk(tree).rows
        forward = build_table(rows, generated=FROZEN)
        backward = build_table(rows[::-1], generated=FROZEN)
        assert forward.equals(backward)

    def test_near_collections_sort_together(self):
        def at(collection_id, lon, lat):
            doc = collection(collection_id, extent=extent([lon, lat, lon + 1, lat + 1]))
            return build_row("example", f"https://ex.org/{collection_id}", doc, [])

        rows = [
            at("oslo", 10, 59),
            at("sydney", 151, -34),
            at("bergen", 5, 60),
            at("perth", 115, -32),
        ]
        ids = build_table(rows, generated=FROZEN).column("id").to_pylist()
        assert abs(ids.index("oslo") - ids.index("bergen")) == 1
        assert abs(ids.index("sydney") - ids.index("perth")) == 1


class TestFile:
    def test_round_trips_through_parquet(self, tree, tmp_path):
        table = table_of(tree)
        path = tmp_path / "out" / INDEX_NAME
        write_index(table, path)
        read = read_index(path)
        assert same_rows(table, read)
        assert pq.ParquetFile(path).metadata.num_row_groups == 1

    def test_writes_original_as_parquet_json(self, tree, tmp_path):
        path = tmp_path / INDEX_NAME
        write_index(table_of(tree), path)
        schema = pq.ParquetFile(path).schema
        [column] = [c for c in map(schema.column, range(len(schema))) if c.path == "original"]
        assert str(column.logical_type) == "JSON"

    def test_a_missing_file_reads_as_none(self, tmp_path):
        assert read_index(tmp_path / INDEX_NAME) is None

    def test_a_file_that_is_not_parquet_reads_as_none(self, tmp_path):
        path = tmp_path / INDEX_NAME
        path.write_text("not parquet")
        assert read_index(path) is None

    def test_a_file_of_another_layout_reads_as_none(self, tmp_path):
        path = tmp_path / INDEX_NAME
        pq.write_table(pa.table({"id": ["a"]}), path)
        assert read_index(path) is None

    def test_the_generated_time_is_not_a_change(self, tree):
        later = build_table(walk(tree).rows, generated=datetime(2027, 1, 1, tzinfo=UTC))
        assert same_rows(later, table_of(tree))

    def test_a_changed_document_is_a_change(self, tree):
        before = table_of(tree)
        tree.docs["https://ex.org/coastal/collection.json"]["title"] = "Coast"
        assert not same_rows(table_of(tree), before)

    def test_no_previous_file_is_a_change(self, tree):
        assert not same_rows(table_of(tree), None)


class TestCarriedRows:
    @pytest.fixture
    def previous(self, tree, tmp_path):
        rows = walk(tree).rows + walk(tree, catalog_id="other").rows
        path = tmp_path / INDEX_NAME
        write_index(build_table(rows, generated=FROZEN), path)
        return read_index(path)

    def test_a_complete_walk_publishes_its_rows(self, previous):
        rows = [row_of(collection())]
        assert settle_rows("example", Walk(rows), previous) == rows

    def test_a_catalog_that_did_not_answer_keeps_its_rows(self, previous):
        carried = settle_rows("example", None, previous)
        assert len(carried) == 5
        assert {row[REGISTRY_ID] for row in carried} == {"example"}

    def test_an_incomplete_walk_keeps_the_previous_rows(self, previous):
        carried = settle_rows("example", Walk([row_of(collection())], complete=False), previous)
        assert len(carried) == 5

    def test_carried_rows_write_the_same_file_again(self, previous):
        """A night with no answer must not read as a change."""
        rows = previous_rows(previous, "example") + previous_rows(previous, "other")
        assert same_rows(build_table(rows, generated=FROZEN), previous)

    def test_an_incomplete_walk_with_no_previous_rows_publishes_what_it_read(self, previous):
        rows = [row_of(collection())]
        assert settle_rows("new", Walk(rows, complete=False), previous) == rows
        assert settle_rows("new", Walk(rows, complete=False), None) == rows

    def test_no_answer_and_no_previous_rows_gives_nothing(self, previous):
        assert settle_rows("new", None, previous) == []
        assert settle_rows("new", None, None) == []


def test_refuses_rows_that_arrow_cannot_hold():
    check_rows([row_of(collection())])
    with pytest.raises(Exception, match="surrogate"):
        check_rows([row_of(collection(title="half \ud800 pair"))])


def test_the_data_link_names_the_file_by_rel_and_type():
    assert data_link("https://registry.portolan-sdi.org/next/") == {
        "rel": "data",
        "href": "https://registry.portolan-sdi.org/next/collections.parquet",
        "type": MEDIA_TYPE,
        "title": "Catalogs and collections as GeoParquet",
    }
