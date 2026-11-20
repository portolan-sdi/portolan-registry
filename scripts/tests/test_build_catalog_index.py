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


def test_an_unreadable_entry_file_skips_only_its_catalog(tmp_path):
    run = Run(tmp_path)
    run.register("alpha", "a1")
    (run.catalogs / "broken.yaml").write_text("- a list, not a mapping\n")
    (run.catalogs / "bad.yaml").write_text("url: [unclosed\n")
    run.statuses.update(broken="valid", bad="valid")
    assert run.build() == 0
    assert run.ids() == [("alpha", "a1"), ("alpha", "alpha")]


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

    def build(self, *options):
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
            *options,
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
    assert "https://gone.org/catalog.json" not in run.fetcher.calls


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


def test_reports_each_catalog_with_a_problem(run, capsys, tmp_path):
    run.register("alpha", "roads", "rivers")
    run.register("beta", "lakes")
    run.build()
    capsys.readouterr()
    run.fetcher.docs["https://alpha.org/rivers/collection.json"] = requests.Timeout("slow")
    run.fetcher.docs["https://beta.org/catalog.json"] = requests.ConnectionError("reset")
    report = tmp_path / "summary.md"
    report.write_text("Earlier step.\n")
    assert run.build("--summary", str(report), "--annotate") == 0

    out = capsys.readouterr().out.splitlines()
    assert out == [
        "::warning title=Catalog index::alpha: read with holes. 1 document(s) not read, such as "
        "https://alpha.org/rivers/collection.json It keeps 1 row(s) from the previous index.",
        "::warning title=Catalog index::beta: failed. reset "
        "It keeps 2 row(s) from the previous index.",
    ]
    text = report.read_text()
    assert text.startswith("Earlier step.\n## Catalog index\n")
    assert "No row changed. The index keeps its 5 rows." in text
    assert "| beta | Failed | 2 | reset |" in text


def test_reports_the_rows_that_it_writes(run, tmp_path):
    run.register("alpha", "roads")
    report = tmp_path / "summary.md"
    assert run.build("--summary", str(report)) == 0
    assert "The build wrote 2 rows from 1 catalogs." in report.read_text()
    assert "| Read | 1 |" in report.read_text()


def test_reports_an_index_that_it_refuses_to_write(run, tmp_path):
    run.register("alpha", "roads")
    run.fetcher.docs.clear()
    report = tmp_path / "summary.md"
    assert run.build("--summary", str(report)) == 1
    assert "The build wrote no index, because no catalog gave a row." in report.read_text()


def test_prints_no_warning_without_the_option(run, capsys):
    run.register("alpha", "roads")
    run.fetcher.docs["https://alpha.org/catalog.json"] = requests.ConnectionError("reset")
    run.build()
    assert capsys.readouterr().out == ""
