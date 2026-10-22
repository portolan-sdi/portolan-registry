"""The published contract: version handling, the store mapping, and schema parity."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft7Validator

from registry.contract import (
    COVERAGE,
    EXTENSION,
    SITE_URL,
    changelog_head,
    declared_fields,
    line_name,
    line_of,
    load_schema,
    parse_version,
    project,
    publish,
    released_versions,
    schema_url,
    split_coverage,
)
from registry.export import PREFIX
from registry.geoparquet import FIELD_TYPES

REPO = Path(__file__).resolve().parents[2]
BASE = f"{SITE_URL}/v0.1/"


def store(**fields):
    return {
        "type": "Catalog",
        "stac_version": "1.1.0",
        "id": "portolan-registry",
        "generated": "2026-01-15T12:00:00+00:00",
        "count": 1,
        "links": [
            {"rel": "root", "href": "https://raw.example/catalogs.json"},
            {"rel": "self", "href": "https://raw.example/catalogs.json"},
            {
                "rel": "child",
                "href": "https://ex.org/catalog.json",
                "title": "A",
                "portolan_registry:id": "a",
                "portolan_registry:licenses": {"MIT": 1, "CC-BY-4.0": 3},
                **fields,
            },
        ],
    }


def child(doc):
    return next(link for link in doc["links"] if link["rel"] == "child")


class TestVersions:
    def test_parses_with_and_without_the_v(self):
        assert parse_version("1.2.3") == (1, 2, 3)
        assert parse_version("v10.0.1") == (10, 0, 1)

    @pytest.mark.parametrize("text", ["1.2", "v1.2.3.4", "one", ""])
    def test_rejects_what_is_not_a_version(self, text):
        with pytest.raises(ValueError, match="not a version"):
            parse_version(text)

    def test_reads_released_directories_in_order(self, tmp_path):
        for name in ("v1.10.0", "v1.2.0", "v2.0.0", "notes", "v3"):
            (tmp_path / name).mkdir()
        (tmp_path / "v9.9.9").write_text("a file, not a release")
        assert released_versions(tmp_path) == [(1, 2, 0), (1, 10, 0), (2, 0, 0)]

    def test_no_release_directory_means_no_release(self, tmp_path):
        assert released_versions(tmp_path / "missing") == []

    def test_before_1_0_each_minor_is_a_line(self):
        assert [line_name(line_of(v)) for v in ((0, 1, 3), (0, 2, 0), (1, 4, 0))] == [
            "v0.1",
            "v0.2",
            "v1",
        ]

    def test_line_of(self):
        assert line_of((0, 1, 5)) == (0, 1)
        assert line_of((2, 3, 4)) == (2,)

    def test_reads_the_top_changelog_heading(self, tmp_path):
        path = tmp_path / "CHANGELOG.md"
        path.write_text("# Log\n\n## [1.1.0] - Unreleased\n\n## [1.0.0] - 2026-10-22\n")
        assert changelog_head(path) == ((1, 1, 0), False)
        path.write_text("# Log\n\n## [1.0.0] - 2026-10-22\n")
        assert changelog_head(path) == ((1, 0, 0), True)
        path.write_text("# Log\n")
        with pytest.raises(ValueError, match="no '## \\[X.Y.Z\\]"):
            changelog_head(path)

    @pytest.mark.parametrize("label", ["2026-10-22 (yanked)", "soon", "2026-02-30", "20261022"])
    def test_a_top_heading_needs_a_date_or_unreleased(self, tmp_path, label):
        path = tmp_path / "CHANGELOG.md"
        path.write_text(f"## [1.0.0] - {label}\n")
        with pytest.raises(ValueError, match="must end in 'Unreleased' or a YYYY-MM-DD date"):
            changelog_head(path)

    def test_the_committed_changelog_names_a_version(self):
        version, _ = changelog_head(REPO / "schema" / "CHANGELOG.md")
        assert version >= (0, 1, 0)

    def test_urls_name_the_version(self):
        assert schema_url(EXTENSION, None) == f"{SITE_URL}/next/schemas/extension.json"
        assert schema_url(COVERAGE, (1, 2, 3)) == f"{SITE_URL}/schemas/v1.2.3/coverage.json"


class TestPublish:
    def test_renames_the_root_fields_and_declares_the_extension(self):
        doc = publish(store(), base_url=BASE, extension_url="https://ext/v1.json")
        assert doc[PREFIX + "generated"] == "2026-01-15T12:00:00+00:00"
        assert doc[PREFIX + "count"] == 1
        assert "generated" not in doc and "count" not in doc
        assert doc["stac_extensions"] == ["https://ext/v1.json"]

    def test_points_root_and_self_at_the_published_file(self):
        doc = publish(store(), base_url=BASE, extension_url="x")
        hrefs = {link["rel"]: link["href"] for link in doc["links"] if link["rel"] != "child"}
        assert hrefs == {"root": f"{BASE}catalogs.json", "self": f"{BASE}catalogs.json"}

    def test_writes_licenses_as_a_sorted_array(self):
        link = child(publish(store(), base_url=BASE, extension_url="x"))
        assert link[PREFIX + "licenses"] == [
            {"id": "CC-BY-4.0", "count": 3},
            {"id": "MIT", "count": 1},
        ]

    def test_an_empty_license_mix_is_an_empty_array(self):
        doc = store()
        child(doc)[PREFIX + "licenses"] = {}
        assert child(publish(doc, base_url=BASE, extension_url="x"))[PREFIX + "licenses"] == []

    def test_links_the_coverage_file(self):
        link = child(publish(store(), base_url=BASE, extension_url="x"))
        assert link[PREFIX + "coverage"] == f"{BASE}coverage/a.json"

    def test_leaves_the_store_unchanged(self):
        original = store()
        kept = copy.deepcopy(original)
        publish(original, base_url=BASE, extension_url="x")
        assert original == kept


class TestProject:
    EXTENSION = {
        "definitions": {
            "root": {"properties": {PREFIX + "count": {}}},
            "link": {"then": {"properties": {"bbox": {}, PREFIX + "id": {}}}},
        }
    }

    def test_reads_the_declared_fields(self):
        assert declared_fields(self.EXTENSION) == ({PREFIX + "count"}, {PREFIX + "id"})

    def test_drops_fields_the_release_does_not_declare(self):
        doc = publish(store(**{PREFIX + "new_field": 1}), base_url=BASE, extension_url="x")
        projected = project(doc, self.EXTENSION)
        assert PREFIX + "generated" not in projected
        assert projected[PREFIX + "count"] == 1
        assert set(child(projected)) == {"rel", "href", "title", PREFIX + "id"}

    def test_keeps_stac_fields_and_other_links(self):
        doc = publish(store(), base_url=BASE, extension_url="x")
        projected = project(doc, self.EXTENSION)
        assert projected["stac_extensions"] == ["x"]
        assert [link["rel"] for link in projected["links"]] == ["root", "self", "child"]


class TestSplitCoverage:
    COVERAGE = {
        "generated": "2026-01-15T12:00:00+00:00",
        "catalogs": [
            {
                "id": "a",
                "collection_count": 2,
                "collections": [{"id": "c", "title": "C", "bbox": [0, 0, 1, 1]}],
            },
            {"id": "b"},
        ],
    }

    def test_writes_one_file_per_catalog_and_an_index(self):
        index, files = split_coverage(self.COVERAGE, base_url=BASE, coverage_schema_url="S")
        assert index == {
            "$schema": "S",
            "generated": "2026-01-15T12:00:00+00:00",
            "catalogs": [
                {"id": "a", "collection_count": 2, "href": f"{BASE}coverage/a.json"},
                {"id": "b", "collection_count": 0, "href": f"{BASE}coverage/b.json"},
            ],
        }
        assert files["a"]["$schema"] == "S"
        assert files["a"]["collections"] == self.COVERAGE["catalogs"][0]["collections"]
        assert files["b"]["collections"] == []


class TestSchemas:
    @pytest.mark.parametrize("name", [EXTENSION, COVERAGE])
    def test_is_valid_draft_7(self, name):
        Draft7Validator.check_schema(load_schema(name, None, root=REPO))

    @pytest.mark.parametrize("name", [EXTENSION, COVERAGE])
    def test_working_id_is_the_next_url(self, name):
        assert load_schema(name, None, root=REPO)["$id"] == schema_url(name, None)

    def test_extension_requires_its_own_url(self):
        extension = load_schema(EXTENSION, None, root=REPO)
        contains = extension["definitions"]["stac_extensions"]["properties"]["stac_extensions"]
        assert contains["contains"]["const"] == extension["$id"]

    def test_coverage_names_its_own_url(self):
        coverage = load_schema(COVERAGE, None, root=REPO)
        assert coverage["properties"]["$schema"]["const"] == coverage["$id"]

    def test_extension_and_producer_schema_declare_the_same_link_fields(self):
        """The producer schema guards the store and the extension guards the
        published file. A field in one and not the other is a field that the
        crawler writes but the contract hides, or the reverse."""
        producer = json.loads((REPO / "schema" / "export.schema.json").read_text())
        stored = {
            name
            for name in producer["$defs"]["link"]["allOf"][0]["then"]["properties"]
            if name.startswith(PREFIX)
        }
        _, published = declared_fields(load_schema(EXTENSION, None, root=REPO))
        # publish() adds the coverage href. Nothing else differs.
        assert published - stored == {PREFIX + "coverage"}
        assert stored - published == set()

    def test_every_link_field_has_a_parquet_type(self):
        _, published = declared_fields(load_schema(EXTENSION, None, root=REPO))
        assert set(FIELD_TYPES) == published

    def test_the_published_store_passes_the_working_extension(self):
        with open(REPO / "exports" / "catalogs.json") as f:
            export = json.load(f)
        extension = load_schema(EXTENSION, None, root=REPO)
        doc = publish(export, base_url=BASE, extension_url=extension["$id"])
        errors = [e.message for e in Draft7Validator(extension).iter_errors(doc)]
        assert errors == []

    def test_the_extension_is_open(self):
        """A consumer that validates with an old copy must accept a new field."""
        extension = load_schema(EXTENSION, None, root=REPO)
        doc = publish(store(), base_url=BASE, extension_url=extension["$id"])
        doc["portolan_registry:a_later_root_field"] = 1
        child(doc)["portolan_registry:a_later_link_field"] = 1
        messages = [e.message for e in Draft7Validator(extension).iter_errors(doc)]
        assert not any("a_later" in message for message in messages)

    @pytest.mark.parametrize("catalog_id", ["../a", "A", "a/b", ""])
    def test_a_catalog_id_must_be_a_slug(self, catalog_id):
        """The id names the coverage file and a URL path segment."""
        extension = load_schema(EXTENSION, None, root=REPO)
        doc = publish(store(**{PREFIX + "id": catalog_id}), base_url=BASE, extension_url="x")
        paths = [list(e.absolute_path) for e in Draft7Validator(extension).iter_errors(doc)]
        assert ["links", 2, PREFIX + "id"] in paths

        coverage = load_schema(COVERAGE, None, root=REPO)
        index, files = split_coverage(
            {"generated": "2026-01-15T12:00:00+00:00", "catalogs": [{"id": catalog_id}]},
            base_url=BASE,
            coverage_schema_url=coverage["$id"],
        )
        validator = Draft7Validator(coverage)
        assert not validator.is_valid(files[catalog_id])
        assert not validator.is_valid(index)
