"""The Pages site build: trees, projection, and the checks before a deploy."""

from __future__ import annotations

import json
import shutil
from datetime import date
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
    return root


def build(root, **kwargs):
    out = root.parent / "_site"
    trees = build_site.build_site(out=out, root=root, today=TODAY, stac_validator=False, **kwargs)
    return out, trees


def read(path):
    return json.loads(path.read_text())


def first_child(doc):
    return next(link for link in doc["links"] if link["rel"] == "child")


def release(root, version="1.0.0"):
    (root / "schema" / "CHANGELOG.md").write_text(f"## [{version}] - Unreleased\n")
    release_contract.release(version, root=root, today=TODAY)


def store_ids(root):
    return {
        link[PREFIX + "id"]
        for link in read(root / "exports" / "catalogs.json")["links"]
        if link["rel"] == "child"
    }


class TestBeforeTheFirstRelease:
    def test_writes_next_only(self, root):
        out, trees = build(root)
        assert trees == [Tree("next", None)]
        assert (out / "CNAME").read_text() == "registry.portolan-sdi.org\n"
        assert (out / "next" / "schemas" / "extension.json").is_file()
        assert not (out / "schemas").exists()
        assert not (out / "v1").exists()

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

    def test_replaces_an_old_build(self, root):
        out = root.parent / "_site"
        (out / "stale").mkdir(parents=True)
        build(root)
        assert not (out / "stale").exists()


class TestAfterARelease:
    def test_writes_the_release_and_its_tree(self, root):
        release(root)
        out, trees = build(root)
        assert trees == [Tree("next", None), Tree("v1", (1, 0, 0))]
        assert (out / "schemas" / "v1.0.0" / "extension.json").is_file()
        doc = read(out / "v1" / "catalogs.json")
        assert doc["stac_extensions"] == [f"{SITE_URL}/schemas/v1.0.0/extension.json"]
        assert "v1/catalogs.json" in (out / "index.html").read_text()

    def test_v1_keeps_its_shape_while_next_gains_a_field(self, root, monkeypatch):
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
        v1_child = first_child(read(out / "v1" / "catalogs.json"))
        assert next_child[PREFIX + "new_field"] == "x"
        assert PREFIX + "new_field" not in v1_child
        v1_columns = pq.read_schema(out / "v1" / "catalogs.parquet").names
        assert PREFIX + "new_field" not in v1_columns

    def test_a_breaking_store_change_fails_the_released_tree(self, root):
        """A field v1 requires that the store stops writing must stop the deploy."""
        release(root)
        store = read(root / "exports" / "catalogs.json")
        for link in store["links"]:
            link.pop(PREFIX + "has_readme", None)
        (root / "exports" / "catalogs.json").write_text(json.dumps(store))
        with pytest.raises(BuildError, match="v1/catalogs.json.*has_readme"):
            build(root)


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
        assert build_site.main(["--out", "site", "--no-stac-validator"]) == 0
        assert (root / "site" / "next" / "catalogs.json").is_file()

    def test_stac_validator_findings_stop_the_build(self, root, monkeypatch):
        finding = validators.Finding("stac-node-validator", "core", "d", "", "bad link")
        monkeypatch.setattr(
            build_site, "run_stac_node_validator", lambda path, schema_map: [finding]
        )
        with pytest.raises(BuildError, match="next/catalogs.json: core: bad link"):
            build_site.build_site(out=root.parent / "_site", root=root, today=TODAY)

    def test_maps_the_extension_to_the_local_schema(self, root, monkeypatch):
        seen = {}

        def fake(path, schema_map):
            seen.update(schema_map)
            return []

        monkeypatch.setattr(build_site, "run_stac_node_validator", fake)
        build_site.build_site(out=root.parent / "_site", root=root, today=TODAY)
        assert seen == {
            f"{SITE_URL}/next/schemas/extension.json": root / "schema" / "extension.json"
        }


@pytest.mark.skipif(not NODE, reason="stac-node-validator is not installed")
def test_the_built_site_passes_stac_node_validator(root):
    release(root)
    out = root.parent / "_site"
    build_site.build_site(out=out, root=root, today=TODAY)
