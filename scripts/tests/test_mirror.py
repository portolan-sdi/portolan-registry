"""The metadata mirror the validators read.

What matters is the tree on disk: rashid resolves every relative href against
it, so a file in the wrong place or a file left out turns into a false error.
"""

from __future__ import annotations

import json

import pytest
from conftest import FROZEN, FakeFetcher
from registry.crawl import crawl_catalog
from registry.mirror import Mirror, MirrorFull, MirroringFetcher, complete_mirror

ROOT = "https://ex.org/catalog.json"


def files(root):
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())


def mirrored(tree, tmp_path, root=ROOT):
    mirror = Mirror(root, tmp_path)
    crawl_catalog(root, MirroringFetcher(tree, mirror), now=FROZEN)
    return mirror


class TestPathFor:
    def test_maps_a_url_to_its_path_under_the_root_directory(self, tmp_path):
        mirror = Mirror("https://ex.org/a/b/catalog.json", tmp_path)
        assert mirror.path_for("https://ex.org/a/b/sub/collection.json") == (
            tmp_path / "sub" / "collection.json"
        )

    def test_a_root_at_the_host_top_level_maps_too(self, tmp_path):
        mirror = Mirror(ROOT, tmp_path)
        assert mirror.path_for(ROOT) == tmp_path / "catalog.json"

    @pytest.mark.parametrize(
        "url",
        [
            "https://other.org/a/b/x.json",
            "http://ex.org/a/b/x.json",
            "https://ex.org/a/c/x.json",
            "https://ex.org/a/b/../../../etc/passwd",
            "https://ex.org/a/b",
            "https://ex.org/a/bb/x.json",
        ],
    )
    def test_a_url_outside_the_tree_has_no_path(self, tmp_path, url):
        mirror = Mirror("https://ex.org/a/b/catalog.json", tmp_path)
        assert mirror.path_for(url) is None


class TestMirroringFetcher:
    def test_writes_every_document_the_crawl_reads(self, tree, tmp_path):
        mirrored(tree, tmp_path)
        assert files(tmp_path) == [
            "catalog.json",
            "coastal/collection.json",
            "sub/alpine/collection.json",
            "sub/catalog.json",
            "sub/inland/collection.json",
        ]

    def test_writes_the_document_as_returned(self, tree, tmp_path):
        mirrored(tree, tmp_path)
        written = json.loads((tmp_path / "coastal/collection.json").read_text())
        assert written == tree.docs["https://ex.org/coastal/collection.json"]

    def test_costs_the_crawl_no_requests(self, tree, tmp_path):
        bare = FakeFetcher(docs=dict(tree.docs), heads=dict(tree.heads))
        crawl_catalog(ROOT, bare, now=FROZEN)
        mirrored(tree, tmp_path)
        assert tree.calls == bare.calls

    def test_records_a_child_that_failed_as_attempted(self, tree, tmp_path):
        tree.docs["https://ex.org/sub/catalog.json"] = TimeoutError("slow")
        mirror = mirrored(tree, tmp_path)
        assert "https://ex.org/sub/catalog.json" in mirror.attempted
        assert not (tmp_path / "sub/catalog.json").exists()


class TestCompleteMirror:
    def test_fetches_items_and_the_documents_beside_each_container(self, tree, tmp_path):
        tree.docs["https://ex.org/coastal/items/coastal-1.json"] = {
            "type": "Feature",
            "id": "c1",
        }
        tree.docs["https://ex.org/AGENTS.md"] = "# Agents\n"
        tree.docs["https://ex.org/README.md"] = "# Example\n"
        mirror = mirrored(tree, tmp_path)
        complete_mirror(mirror, lambda: tree)
        got = files(tmp_path)
        assert "AGENTS.md" in got
        assert "README.md" in got
        assert "coastal/items/coastal-1.json" in got
        assert mirror.failures == []

    def test_a_missing_file_is_left_for_rashid_to_report(self, tree, tmp_path):
        """A 404 is a fact about the catalog. It is not a fetch failure."""
        mirror = mirrored(tree, tmp_path)
        complete_mirror(mirror, lambda: tree)
        assert mirror.failures == []
        assert not (tmp_path / "coastal/AGENTS.md").exists()

    def test_a_server_error_is_a_failure(self, tree, tmp_path):
        tree.docs["https://ex.org/coastal/items/coastal-2.json"] = ConnectionError("520")
        mirror = mirrored(tree, tmp_path)
        complete_mirror(mirror, lambda: tree)
        assert mirror.failures == ["https://ex.org/coastal/items/coastal-2.json: 520"]

    def test_does_not_refetch_what_the_crawl_read(self, tree, tmp_path):
        mirror = mirrored(tree, tmp_path)
        crawled = {c for c in tree.calls if c.startswith("https://")}
        complete_mirror(mirror, lambda: tree)
        completed = [c[len("BYTES ") :] for c in tree.calls if c.startswith("BYTES ")]
        assert not crawled & set(completed)
        assert len(completed) == len(set(completed))

    def test_does_not_retry_a_child_the_crawl_could_not_fetch(self, tree, tmp_path):
        tree.docs["https://ex.org/sub/catalog.json"] = TimeoutError("slow")
        mirror = mirrored(tree, tmp_path)
        complete_mirror(mirror, lambda: tree)
        assert tree.calls.count("BYTES https://ex.org/sub/catalog.json") == 0

    def test_follows_a_child_the_crawl_does_not(self, tmp_path):
        """The crawl reads catalog children only. A collection's child still
        has to be on disk, or rashid reports its link as unresolved."""
        f = FakeFetcher(
            docs={
                ROOT: {"type": "Catalog", "links": [{"rel": "child", "href": "./a/collection.json"}]},
                "https://ex.org/a/collection.json": {
                    "type": "Collection",
                    "links": [{"rel": "child", "href": "./b/collection.json"}],
                },
                "https://ex.org/a/b/collection.json": {
                    "type": "Collection",
                    "links": [{"rel": "item", "href": "./i.json"}],
                },
                "https://ex.org/a/b/i.json": {"type": "Feature", "id": "i"},
            }
        )
        mirror = mirrored(f, tmp_path)
        complete_mirror(mirror, lambda: f)
        assert "a/b/collection.json" in files(tmp_path)
        assert "a/b/i.json" in files(tmp_path)

    def test_keeps_a_document_that_does_not_parse(self, tree, tmp_path):
        tree.docs["https://ex.org/coastal/items/coastal-1.json"] = b"{not json"
        mirror = mirrored(tree, tmp_path)
        complete_mirror(mirror, lambda: tree)
        assert (tmp_path / "coastal/items/coastal-1.json").read_bytes() == b"{not json"

    def test_leaves_a_link_outside_the_tree_unmirrored(self, tmp_path):
        f = FakeFetcher(
            docs={
                ROOT: {
                    "type": "Catalog",
                    "links": [{"rel": "item", "href": "https://other.org/i.json"}],
                },
                "https://other.org/i.json": {"type": "Feature"},
            }
        )
        mirror = mirrored(f, tmp_path)
        complete_mirror(mirror, lambda: f)
        assert "https://other.org/i.json" in mirror.outside
        assert "BYTES https://other.org/i.json" not in f.calls

    def test_builds_one_source_per_worker(self, tree, tmp_path):
        built = []

        def factory():
            built.append(1)
            return tree

        mirror = mirrored(tree, tmp_path)
        complete_mirror(mirror, factory, workers=2)
        assert 1 <= len(built) <= 2


class TestLimits:
    def test_a_write_that_fails_is_recorded_and_does_not_raise(self, tmp_path):
        mirror = Mirror(ROOT, tmp_path)
        mirror.write("https://ex.org/a", b"{}")
        mirror.write("https://ex.org/a/item.json", b"{}")
        assert len(mirror.unwritable) == 1
        assert mirror.unwritable[0].startswith("https://ex.org/a/item.json: ")
        assert "could not be written" in mirror.problem()

    def test_a_name_too_long_for_the_file_system_is_recorded(self, tmp_path):
        mirror = Mirror(ROOT, tmp_path)
        mirror.write("https://ex.org/" + "x" * 300 + ".json", b"{}")
        assert len(mirror.unwritable) == 1

    def test_the_crawl_stops_at_the_limit(self, tree, tmp_path):
        mirror = Mirror(ROOT, tmp_path, max_documents=2)
        result = crawl_catalog(ROOT, MirroringFetcher(tree, mirror), now=FROZEN)
        assert len(mirror.attempted) == 2
        assert mirror.truncated
        assert any("mirror is full" in f for f in result["fetch_failures"])

    def test_the_completion_stops_at_the_limit(self, tree, tmp_path):
        mirror = mirrored(tree, tmp_path)
        mirror.max_documents = len(mirror.attempted) + 3
        complete_mirror(mirror, lambda: tree)
        assert len(mirror.attempted) == mirror.max_documents
        assert "more than" in mirror.problem()

    def test_a_complete_mirror_has_no_problem(self, tree, tmp_path):
        mirror = mirrored(tree, tmp_path)
        complete_mirror(mirror, lambda: tree)
        assert mirror.problem() is None

    def test_mirror_full_is_a_request_error(self):
        import requests

        assert issubclass(MirrorFull, requests.RequestException)
