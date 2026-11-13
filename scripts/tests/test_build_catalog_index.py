"""The script that builds the catalog index from the registered catalogs."""

from __future__ import annotations

import json

import build_catalog_index
import pyarrow.parquet as pq
import pytest
import requests
from conftest import FakeFetcher

from registry.catalog_index import REGISTRY_ID
from registry.export import PREFIX


def tree(name, *collections):
    """The documents of one small catalog at https://<name>.org/."""
    base = f"https://{name}.org"
    links = [{"rel": "child", "href": f"./{c}/collection.json"} for c in collections]
    docs = {
        f"{base}/catalog.json": {
            "type": "Catalog",
            "id": name,
            "description": "d",
            "links": links,
        }
    }
    for c in collections:
        docs[f"{base}/{c}/collection.json"] = {
            "type": "Collection",
            "id": c,
            "description": "d",
            "license": "CC0-1.0",
            "extent": {
                "spatial": {"bbox": [[0, 0, 1, 1]]},
                "temporal": {"interval": [[None, None]]},
            },
            "links": [],
        }
    return docs


class Run:
    """A registry directory, the documents its catalogs serve, and the script."""

    def __init__(self, tmp_path):
        self.catalogs = tmp_path / "catalogs"
        self.catalogs.mkdir()
        self.export = tmp_path / "catalogs.json"
        self.output = tmp_path / "exports" / "collections.parquet"
        self.fetcher = FakeFetcher()
        self.statuses = {}

    def register(self, name, *collections, status="valid"):
        (self.catalogs / f"{name}.yaml").write_text(f"url: https://{name}.org/catalog.json\n")
        self.fetcher.docs.update(tree(name, *collections))
        self.statuses[name] = status

    def build(self):
        links = [
            {"rel": "child", PREFIX + "id": name, PREFIX + "status": status}
            for name, status in self.statuses.items()
        ]
        self.export.write_text(json.dumps({"links": links}))
        argv = [
            "--output",
            str(self.output),
            "--catalog-dir",
            str(self.catalogs),
            "--export",
            str(self.export),
        ]
        return build_catalog_index.main(argv, fetcher_factory=lambda: self.fetcher)

    def ids(self):
        table = pq.read_table(self.output, columns=["id", REGISTRY_ID])
        rows = table.to_pylist()
        return sorted((row[REGISTRY_ID], row["id"]) for row in rows)


@pytest.fixture
def run(tmp_path):
    return Run(tmp_path)


def test_writes_the_rows_of_every_registered_catalog(run):
    run.register("alpha", "roads")
    run.register("beta", "roads", "rivers")
    assert run.build() == 0
    assert run.ids() == [
        ("alpha", "alpha"),
        ("alpha", "roads"),
        ("beta", "beta"),
        ("beta", "rivers"),
        ("beta", "roads"),
    ]


def test_an_unchanged_registry_leaves_the_file_untouched(run, capsys):
    run.register("alpha", "roads")
    run.build()
    before = run.output.read_bytes()
    assert run.build() == 0
    assert run.output.read_bytes() == before
    assert "No change in 2 row(s)" in capsys.readouterr().err


def test_a_changed_document_rewrites_the_file(run):
    run.register("alpha", "roads")
    run.build()
    before = run.output.read_bytes()
    run.fetcher.docs["https://alpha.org/roads/collection.json"]["title"] = "Roads"
    assert run.build() == 0
    assert run.output.read_bytes() != before


def test_a_removed_catalog_has_no_rows(run):
    run.register("alpha", "roads")
    run.register("gone", "roads", status="removed")
    run.build()
    assert {catalog_id for catalog_id, _ in run.ids()} == {"alpha"}
    assert not [call for call in run.fetcher.calls if "gone.org" in call]


def test_a_catalog_that_does_not_answer_keeps_its_rows(run, capsys):
    run.register("alpha", "roads")
    run.register("beta", "rivers")
    run.build()
    before = run.output.read_bytes()
    run.fetcher.docs["https://beta.org/catalog.json"] = requests.ConnectionError("reset")
    assert run.build() == 0
    assert run.output.read_bytes() == before
    err = capsys.readouterr().err
    assert "beta: FAILED: reset" in err
    assert "Carrying forward 2 row(s) of beta" in err


def test_a_child_that_fails_keeps_the_previous_rows(run, capsys):
    run.register("alpha", "roads", "rivers")
    run.build()
    run.fetcher.docs["https://alpha.org/rivers/collection.json"] = requests.Timeout("slow")
    assert run.build() == 0
    assert ("alpha", "rivers") in run.ids()
    assert "alpha: 2 row(s), incomplete" in capsys.readouterr().err


def test_a_catalog_that_arrow_cannot_hold_does_not_stop_the_others(run, capsys):
    """One document with text that is not valid Unicode must not stop the index."""
    run.register("alpha", "roads")
    run.register("beta", "rivers")
    run.build()
    run.fetcher.docs["https://beta.org/rivers/collection.json"]["title"] = "half \ud800 pair"
    run.fetcher.docs["https://alpha.org/roads/collection.json"]["title"] = "Roads"
    assert run.build() == 0
    assert "beta: FAILED" in capsys.readouterr().err
    table = pq.read_table(run.output, columns=["id", "title"]).to_pylist()
    assert {row["id"]: row["title"] for row in table} == {
        "alpha": None,
        "roads": "Roads",
        "beta": None,
        "rivers": None,
    }


def test_refuses_to_write_an_empty_index(run, capsys):
    run.register("alpha", "roads")
    run.fetcher.docs.clear()
    assert run.build() == 1
    assert not run.output.exists()
    assert "REFUSING TO WRITE THE INDEX" in capsys.readouterr().err


def test_an_entry_without_a_url_is_skipped(run, capsys):
    run.register("alpha", "roads")
    (run.catalogs / "empty.yaml").write_text("submitter_email: a@example.org\n")
    assert run.build() == 0
    assert {catalog_id for catalog_id, _ in run.ids()} == {"alpha"}
    assert "empty: skipping, the entry has no URL" in capsys.readouterr().err
