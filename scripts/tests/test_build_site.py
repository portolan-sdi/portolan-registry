"""The Pages site build: trees, projection, and the checks before a deploy."""

from __future__ import annotations

import json
import shutil
from datetime import date
from html.parser import HTMLParser
from pathlib import Path

import build_site
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import release_contract
from build_site import BuildError, Tree

from registry import geoparquet, validators
from registry.contract import SITE_URL
from registry.export import PREFIX

REPO = Path(__file__).resolve().parents[2]
TODAY = date(2026, 10, 22)

NODE = (
    shutil.which("node")
    and (validators.SNV_RUNNER.parent / "node_modules" / "stac-node-validator").is_dir()
)


@pytest.fixture
def root(tmp_path):
    """A repository copy with the committed store and the working schemas."""
    root = tmp_path / "repo"
    shutil.copytree(REPO / "schema", root / "schema")
    shutil.copytree(REPO / "exports", root / "exports")
    shutil.copytree(REPO / "site", root / "site")
    return root


def build(root, **kwargs):
    out = root.parent / "_site"
    trees = build_site.build_site(out=out, root=root, stac_validator=False, **kwargs)
    return out, trees


def read(path):
    return json.loads(path.read_text())


def first_child(doc):
    return next(link for link in doc["links"] if link["rel"] == "child")


def release(root, version="0.1.0"):
    (root / "schema" / "CHANGELOG.md").write_text(f"## [{version}] - Unreleased\n")
    release_contract.release(version, root=root, today=TODAY)


def drop_has_readme(root):
    """A breaking change: the schema and the store both lose a required field."""
    path = root / "schema" / "extension.json"
    schema = read(path)
    then = schema["definitions"]["link"]["then"]
    then["required"].remove(PREFIX + "has_readme")
    del then["properties"][PREFIX + "has_readme"]
    path.write_text(json.dumps(schema))
    store = read(root / "exports" / "catalogs.json")
    for link in store["links"]:
        link.pop(PREFIX + "has_readme", None)
    (root / "exports" / "catalogs.json").write_text(json.dumps(store))


def store_ids(root):
    return {
        link[PREFIX + "id"]
        for link in read(root / "exports" / "catalogs.json")["links"]
        if link["rel"] == "child"
    }


class _Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.local = []

    def handle_starttag(self, tag, attrs):
        for name, value in attrs:
            if name in ("href", "src") and value and "://" not in value:
                self.local.append(value)


def local_links(page):
    parser = _Links()
    parser.feed(page)
    return parser.local


class TestBeforeTheFirstRelease:
    def test_writes_next_only(self, root):
        out, trees = build(root)
        assert trees == [Tree("next", None)]
        assert (out / "CNAME").read_text() == "registry.portolan-sdi.org\n"
        assert (out / "next" / "schemas" / "extension.json").is_file()
        assert not (out / "schemas").exists()
        assert not (out / "v0.1").exists()

    def test_next_declares_the_working_schema(self, root):
        out, _ = build(root)
        doc = read(out / "next" / "catalogs.json")
        assert doc["stac_extensions"] == [f"{SITE_URL}/next/schemas/extension.json"]
        self_link = next(link for link in doc["links"] if link["rel"] == "self")
        assert self_link["href"] == f"{SITE_URL}/next/catalogs.json"

    def test_writes_compact_json(self, root):
        out, _ = build(root)
        assert "\n" not in (out / "next" / "catalogs.json").read_text()

    def test_splits_coverage_one_file_per_catalog(self, root):
        out, _ = build(root)
        index = read(out / "next" / "coverage" / "index.json")
        ids = {entry["id"] for entry in index["catalogs"]}
        assert ids == store_ids(root)
        for catalog_id in ids:
            record = read(out / "next" / "coverage" / f"{catalog_id}.json")
            assert record["$schema"] == f"{SITE_URL}/next/schemas/coverage.json"

    def test_parquet_has_one_row_per_catalog(self, root):
        out, _ = build(root)
        table = pq.read_table(out / "next" / "catalogs.parquet")
        assert table.num_rows == len(store_ids(root))
        assert set(table.column("portolan_registry:id").to_pylist()) == store_ids(root)

    def test_parquet_keeps_the_link_type(self, root):
        out, _ = build(root)
        types = pq.read_table(out / "next" / "catalogs.parquet").column("type").to_pylist()
        assert set(types) == {"application/json"}

    def test_replaces_an_old_build(self, root):
        out, _ = build(root)
        (out / "stale").mkdir()
        build(root)
        assert not (out / "stale").exists()


class TestAfterARelease:
    def test_writes_the_release_and_its_tree(self, root):
        release(root)
        out, trees = build(root)
        assert trees == [Tree("next", None), Tree("v0.1", (0, 1, 0))]
        assert (out / "schemas" / "v0.1.0" / "extension.json").is_file()
        doc = read(out / "v0.1" / "catalogs.json")
        assert doc["stac_extensions"] == [f"{SITE_URL}/schemas/v0.1.0/extension.json"]
        assert "v0.1/catalogs.json" in (out / "index.html").read_text()

    def test_a_release_keeps_its_shape_while_next_gains_a_field(self, root, monkeypatch):
        release(root)
        path = root / "schema" / "extension.json"
        schema = read(path)
        schema["definitions"]["link"]["then"]["properties"][PREFIX + "new_field"] = {
            "type": ["string", "null"]
        }
        path.write_text(json.dumps(schema))
        store = read(root / "exports" / "catalogs.json")
        for link in store["links"]:
            if link["rel"] == "child":
                link[PREFIX + "new_field"] = "x"
        (root / "exports" / "catalogs.json").write_text(json.dumps(store))
        # The parquet writer needs a type for the new field.
        monkeypatch.setitem(geoparquet.FIELD_TYPES, PREFIX + "new_field", pa.string())
        out, _ = build(root)
        next_child = first_child(read(out / "next" / "catalogs.json"))
        v01_child = first_child(read(out / "v0.1" / "catalogs.json"))
        assert next_child[PREFIX + "new_field"] == "x"
        assert PREFIX + "new_field" not in v01_child
        v01_columns = pq.read_schema(out / "v0.1" / "catalogs.parquet").names
        assert PREFIX + "new_field" not in v01_columns

    def test_a_breaking_store_change_fails_the_released_tree(self, root):
        """A breaking change that merges before its release must stop the deploy."""
        release(root)
        drop_has_readme(root)
        with pytest.raises(BuildError, match="v0.1/catalogs.json.*has_readme"):
            build(root)

    def test_a_breaking_release_replaces_the_previous_tree(self, root):
        release(root)
        drop_has_readme(root)
        release(root, "0.2.0")
        out, trees = build(root)
        assert trees == [Tree("next", None), Tree("v0.2", (0, 2, 0))]
        assert not (out / "v0.1").exists()
        assert (out / "schemas" / "v0.1.0" / "extension.json").is_file()
        assert (out / "schemas" / "v0.2.0" / "extension.json").is_file()
        index = (out / "index.html").read_text()
        assert "v0.2/catalogs.json" in index
        assert "v0.1/catalogs.json" not in index

    def test_an_additive_release_keeps_the_line(self, root):
        release(root)
        release(root, "0.1.1")
        out, trees = build(root)
        assert trees == [Tree("next", None), Tree("v0.1", (0, 1, 1))]
        doc = read(out / "v0.1" / "catalogs.json")
        assert doc["stac_extensions"] == [f"{SITE_URL}/schemas/v0.1.1/extension.json"]


class TestChecks:
    def test_refuses_a_value_the_schema_rejects(self, root):
        store = read(root / "exports" / "catalogs.json")
        first_child(store)[PREFIX + "status"] = "bogus"
        (root / "exports" / "catalogs.json").write_text(json.dumps(store))
        with pytest.raises(BuildError, match="next/catalogs.json.*bogus"):
            build(root)

    def test_refuses_a_catalog_without_coverage(self, root):
        coverage = read(root / "exports" / "coverage-bboxes.json")
        coverage["catalogs"] = coverage["catalogs"][1:]
        (root / "exports" / "coverage-bboxes.json").write_text(json.dumps(coverage))
        with pytest.raises(BuildError, match="no coverage file for"):
            build(root)

    def test_refuses_coverage_without_a_catalog(self, root):
        coverage = read(root / "exports" / "coverage-bboxes.json")
        coverage["catalogs"].append({"id": "gone", "collection_count": 0, "collections": []})
        (root / "exports" / "coverage-bboxes.json").write_text(json.dumps(coverage))
        with pytest.raises(BuildError, match="no child link for coverage gone"):
            build(root)

    def test_refuses_a_nested_key_that_parquet_would_drop(self, root):
        store = read(root / "exports" / "catalogs.json")
        first_child(store)[PREFIX + "host"] = {"name": "H", "url": "https://h.org", "roles": []}
        (root / "exports" / "catalogs.json").write_text(json.dumps(store))
        with pytest.raises(BuildError, match="catalogs.parquet: .*portolan_registry:host.roles"):
            build(root)

    def test_refuses_a_value_with_a_bad_format(self, root):
        coverage = read(root / "exports" / "coverage-bboxes.json")
        coverage["generated"] = "yesterday"
        (root / "exports" / "coverage-bboxes.json").write_text(json.dumps(coverage))
        with pytest.raises(BuildError, match="coverage.*'yesterday' is not a 'date-time'"):
            build(root)

    def test_refuses_a_field_without_a_parquet_type(self, root, monkeypatch):
        monkeypatch.delitem(geoparquet.FIELD_TYPES, PREFIX + "host")
        with pytest.raises(BuildError, match="no parquet type .* portolan_registry:host"):
            build(root)

    def test_refuses_to_replace_a_directory_that_is_not_a_build(self, root):
        (root / "keep.txt").write_text("not a build")
        with pytest.raises(BuildError, match="not an earlier site build"):
            build_site.build_site(out=root, root=root, stac_validator=False)
        assert (root / "keep.txt").is_file()

    def test_writes_into_an_empty_directory(self, root):
        (root.parent / "_site").mkdir()
        out, _ = build(root)
        assert (out / "CNAME").is_file()

    def test_refuses_a_file_over_the_limit(self, root, monkeypatch):
        monkeypatch.setattr(build_site, "MAX_FILE_BYTES", 1000)
        with pytest.raises(BuildError, match="files over 50 MB"):
            build(root)

    def test_cli_reports_a_refusal(self, root, monkeypatch, capsys):
        store = read(root / "exports" / "catalogs.json")
        first_child(store)[PREFIX + "status"] = "bogus"
        (root / "exports" / "catalogs.json").write_text(json.dumps(store))
        monkeypatch.chdir(root)
        assert build_site.main(["--out", "site", "--no-stac-validator"]) == 1
        assert "REFUSING TO PUBLISH" in capsys.readouterr().err

    def test_cli_builds(self, root, monkeypatch):
        monkeypatch.chdir(root)
        assert build_site.main(["--out", "_site", "--no-stac-validator"]) == 0
        assert (root / "_site" / "next" / "catalogs.json").is_file()

    def test_stac_validator_findings_stop_the_build(self, root, monkeypatch):
        finding = validators.Finding("stac-node-validator", "core", "d", "", "bad link")
        monkeypatch.setattr(
            build_site, "run_stac_node_validator", lambda path, schema_map: [finding]
        )
        with pytest.raises(BuildError, match="next/catalogs.json: core: bad link"):
            build_site.build_site(out=root.parent / "_site", root=root)

    def test_maps_the_extension_to_the_local_schema(self, root, monkeypatch):
        seen = {}

        def fake(path, schema_map):
            seen.update(schema_map)
            return []

        monkeypatch.setattr(build_site, "run_stac_node_validator", fake)
        build_site.build_site(out=root.parent / "_site", root=root)
        assert seen == {
            f"{SITE_URL}/next/schemas/extension.json": root / "schema" / "extension.json"
        }


@pytest.mark.skipif(not NODE, reason="stac-node-validator is not installed")
def test_the_built_site_passes_stac_node_validator(root):
    release(root)
    out = root.parent / "_site"
    build_site.build_site(out=out, root=root)


class TestIndex:
    def test_every_local_link_resolves(self, root):
        release(root)
        out, _ = build(root)
        links = local_links((out / "index.html").read_text())
        assert "style.css" in links and "_brand-vars.css" in links
        assert [link for link in links if not (out / link).is_file()] == []

    def test_shows_the_count_and_the_store_date(self, root):
        store = read(root / "exports" / "catalogs.json")
        out, _ = build(root)
        page = (out / "index.html").read_text()
        assert f"<dd>{store['count']}</dd>" in page
        assert f">{store['generated'][:10]}</time>" in page

    def test_says_when_no_release_is_published(self, root):
        out, _ = build(root)
        assert "The registry has no releases yet." in (out / "index.html").read_text()

    def test_lists_the_release_line_before_next(self, root):
        release(root)
        out, _ = build(root)
        page = (out / "index.html").read_text()
        assert "The registry has no releases yet." not in page
        assert page.index("<code>v0.1/</code>") < page.index("<code>next/</code>")
        assert 'href="schemas/v0.1.0/extension.json"' in page
