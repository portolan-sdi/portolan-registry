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

    def test_the_root_reports_its_agents_and_readme_links(self, tree):
        r = crawl_catalog(ROOT, tree, now=FROZEN)
        assert r["validation"]["has_agents_md"] is True
        assert r["validation"]["has_readme"] is True

    def test_the_root_logo_is_read_and_resolved(self, tree):
        assert crawl_catalog(ROOT, tree, now=FROZEN)["logo"] == {
            "href": "https://ex.org/_assets/logo.png",
            "type": "image/png",
            "title": "Example",
        }

    def test_a_catalog_without_an_icon_reports_no_logo(self):
        f = FakeFetcher(docs={ROOT: catalog()})
        assert crawl_catalog(ROOT, f, now=FROZEN)["logo"] is None

    def test_a_sub_catalogs_icon_is_not_the_registered_logo(self):
        """Only the catalog someone registered gets listed, so only its own
        branding belongs on the registry."""
        sub = catalog()
        sub["links"].append(
            {"rel": "icon", "href": "./sub.png", "type": "image/png"}
        )
        f = FakeFetcher(
            docs={ROOT: catalog("./sub/catalog.json"), "https://ex.org/sub/catalog.json": sub},
            heads={"https://ex.org/sub/sub.png": {"Content-Type": "image/png"}},
        )
        assert crawl_catalog(ROOT, f, now=FROZEN)["logo"] is None

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
        assert by_id["coastal"].title == "Coastal"
        assert by_id["inland"].row_count == 300


class TestProvenance:
    """The kind and the parties, derived over the whole merged tree.

    The nested fixture is a mirror: coastal and inland each name a producer
    of their own and the same host, and alpine names no providers at all.
    """

    def test_reads_the_kind_over_every_collection(self, tree):
        result = crawl_catalog(ROOT, tree)
        assert result["kind"] == "mirror"

    def test_names_the_producers_of_a_nested_catalog(self, tree):
        # inland sits under the sub-catalog. A derivation done one level at a
        # time would miss it, the way the license mix once did.
        result = crawl_catalog(ROOT, tree)
        assert [p["name"] for p in result["producers"]] == [
            "Coastal Authority",
            "Inland Agency",
        ]

    def test_names_the_host(self, tree):
        assert crawl_catalog(ROOT, tree)["host"] == {
            "name": "Example Host",
            "url": "https://host.example/",
        }

    def test_a_catalog_declaring_no_providers_reports_no_kind(self, unmeasured):
        result = crawl_catalog(ROOT, unmeasured)
        assert result["kind"] is None
        assert result["producers"] == []
        assert result["host"] is None


class TestItemCount:
    """Counting items must stay free.

    A registered catalog's items outnumber its collections by orders of
    magnitude: one catalog alone publishes 4,065 item links against 15
    collections. Counting the links the collection already handed us costs
    nothing; fetching them would multiply the crawl.
    """

    def test_counts_item_links_across_the_tree(self, tree):
        # coastal links 2, inland links 3, alpine links none.
        assert crawl_catalog(ROOT, tree, now=FROZEN)["item_count"] == 5

    def test_never_fetches_an_item(self, tree):
        crawl_catalog(ROOT, tree, now=FROZEN)
        assert not [c for c in tree.calls if "/items/" in c]

    def test_an_items_endpoint_does_not_hide_inline_links(self, tree):
        # coastal publishes rel="items" *and* links its items inline. The
        # endpoint's presence must not mark it unenumerable.
        r = crawl_catalog(ROOT, tree, now=FROZEN)
        coastal = next(c for c in r["collections"] if c.id == "coastal")
        assert coastal.item_count == 2
        assert coastal.items_unenumerable is False
        assert r["counts_partial"] is False

    def test_null_when_no_item_is_countable(self, unmeasured):
        # Every collection holding items hides them behind rel="items", so the
        # honest answer is "not measured", not "zero items".
        r = crawl_catalog(ROOT, unmeasured, now=FROZEN)
        assert r["item_count"] is None
        assert r["counts_partial"] is True

    def test_zero_when_enumerable_and_empty(self):
        # A collection with no items and no endpoint really does have none.
        f = FakeFetcher(
            docs={
                ROOT: catalog("./c/collection.json"),
                "https://ex.org/c/collection.json": collection(links=[]),
            }
        )
        r = crawl_catalog(ROOT, f, now=FROZEN)
        assert r["item_count"] == 0
        assert r["counts_partial"] is False

    def test_a_hidden_branch_does_not_erase_a_counted_one(self):
        # One unenumerable collection must not null out items counted
        # elsewhere. The count becomes a floor, and the flag says so.
        f = FakeFetcher(
            docs={
                ROOT: catalog("./a/collection.json", "./b/collection.json"),
                "https://ex.org/a/collection.json": collection(
                    links=[{"rel": "item", "href": "./items/1.json"}]
                ),
                "https://ex.org/b/collection.json": collection(
                    links=[{"rel": "items", "href": "./items"}]
                ),
            }
        )
        r = crawl_catalog(ROOT, f, now=FROZEN)
        assert r["item_count"] == 1
        assert r["counts_partial"] is True


class TestUnmeasuredSize:
    def test_null_when_nothing_declares_a_size(self, unmeasured):
        r = crawl_catalog(ROOT, unmeasured, now=FROZEN)
        assert r["total_size_bytes"] is None
        # The assets are there; only their sizes are missing.
        assert r["asset_count"] == 2

    def test_keeps_a_partial_measurement(self, tree):
        # coastal declares one size and omits another. A sum from some sized
        # assets is still a measurement, so it stays a number.
        r = crawl_catalog(ROOT, tree, now=FROZEN)
        assert r["total_size_bytes"] == 6144


class TestPartialCrawls:
    def test_a_failed_child_marks_the_counts_partial(self, tree):
        tree.docs["https://ex.org/sub/catalog.json"] = TimeoutError("read timed out")
        r = crawl_catalog(ROOT, tree, now=FROZEN)
        # The crawl still reports what it reached, and admits it is a floor.
        assert r["counts_partial"] is True
        assert r["collection_count"] == 1
        assert r["item_count"] == 2

    def test_a_complete_crawl_is_not_partial(self, tree):
        assert crawl_catalog(ROOT, tree, now=FROZEN)["counts_partial"] is False


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


V010 = "https://schemas.portolan-sdi.org/portolan/v0.1.0/schema.json"
V011 = "https://schemas.portolan-sdi.org/portolan/v0.1.1/schema.json"


class TestSpecVersion:
    """The versioned schema URI is the only signal of specification version."""

    def declaring(self, uri, **kw):
        return {"stac_extensions": [uri], **kw}

    def test_reads_the_version_the_root_declares(self, tree):
        r = crawl_catalog(ROOT, tree, now=FROZEN)
        assert r["spec_version"] == "0.1.0"
        assert r["spec_version_mixed"] is False

    def test_reports_none_when_nothing_is_declared(self):
        f = FakeFetcher(docs={ROOT: catalog()})
        assert crawl_catalog(ROOT, f, now=FROZEN)["spec_version"] is None

    def test_ignores_other_extensions(self):
        doc = catalog()
        doc["stac_extensions"] = [
            "https://stac-extensions.github.io/file/v2.1.0/schema.json"
        ]
        f = FakeFetcher(docs={ROOT: doc})
        assert crawl_catalog(ROOT, f, now=FROZEN)["spec_version"] is None

    def test_ignores_an_unversioned_portolan_uri(self):
        doc = catalog()
        doc["stac_extensions"] = [
            "https://schemas.portolan-sdi.org/portolan/latest/schema.json"
        ]
        f = FakeFetcher(docs={ROOT: doc})
        assert crawl_catalog(ROOT, f, now=FROZEN)["spec_version"] is None

    def test_accepts_a_two_component_version(self):
        """The org owns the URI. A shorter version is a declaration, not
        silence."""
        doc = catalog()
        doc["stac_extensions"] = [
            "https://schemas.portolan-sdi.org/portolan/v0.2/schema.json"
        ]
        f = FakeFetcher(docs={ROOT: doc})
        assert crawl_catalog(ROOT, f, now=FROZEN)["spec_version"] == "0.2"

    def test_a_collection_on_another_version_is_mixed(self):
        f = FakeFetcher(
            docs={
                ROOT: {**catalog("./c/collection.json"), "stac_extensions": [V010]},
                "https://ex.org/c/collection.json": collection(
                    stac_extensions=[V011]
                ),
            }
        )
        r = crawl_catalog(ROOT, f, now=FROZEN)
        assert r["spec_version"] == "0.1.0"
        assert r["spec_version_mixed"] is True

    def test_a_sub_catalog_on_another_version_is_mixed(self):
        f = FakeFetcher(
            docs={
                ROOT: {**catalog("./s/catalog.json"), "stac_extensions": [V010]},
                "https://ex.org/s/catalog.json": {
                    **catalog(),
                    "stac_extensions": [V011],
                },
            }
        )
        assert crawl_catalog(ROOT, f, now=FROZEN)["spec_version_mixed"] is True

    def test_an_undeclared_collection_is_not_a_mismatch(self):
        """Declaring nothing is a conformance failure for the validator, not a
        disagreement about which version this catalog is on."""
        f = FakeFetcher(
            docs={
                ROOT: {**catalog("./c/collection.json"), "stac_extensions": [V010]},
                "https://ex.org/c/collection.json": collection(),
            }
        )
        assert crawl_catalog(ROOT, f, now=FROZEN)["spec_version_mixed"] is False

    def test_the_version_is_kept_per_collection(self, tree):
        r = crawl_catalog(ROOT, tree, now=FROZEN)
        assert all(c.spec_version is None for c in r["collections"])


class TestUpdated:
    def test_passes_the_catalogs_own_updated_through(self, tree):
        assert crawl_catalog(ROOT, tree, now=FROZEN)["updated"] == (
            "2026-01-10T08:30:00Z"
        )

    def test_is_none_when_the_catalog_sets_none(self):
        f = FakeFetcher(docs={ROOT: catalog()})
        assert crawl_catalog(ROOT, f, now=FROZEN)["updated"] is None


class TestRequiredDocuments:
    """PORTO-CORE-061 and PORTO-CORE-062, the two MUST-level Markdown links."""

    def test_a_catalog_without_the_links_reports_neither(self):
        f = FakeFetcher(docs={ROOT: catalog()})
        r = crawl_catalog(ROOT, f, now=FROZEN)
        assert r["validation"]["has_agents_md"] is False
        assert r["validation"]["has_readme"] is False

    def test_a_link_without_the_markdown_type_does_not_count(self):
        """The specification names the relation and the media type together.

        A `describedby` link to an HTML page is a valid STAC link, and it is
        not the README.md the specification asks for.
        """
        doc = {
            "type": "Catalog",
            "links": [
                {"rel": "agents", "href": "./AGENTS.md"},
                {"rel": "describedby", "href": "./about.html", "type": "text/html"},
            ],
        }
        r = crawl_catalog(ROOT, FakeFetcher(docs={ROOT: doc}), now=FROZEN)
        assert r["validation"]["has_agents_md"] is False
        assert r["validation"]["has_readme"] is False

    def test_a_sub_catalogs_links_do_not_answer_for_the_root(self):
        """The registry reports the root it was given, not the tree."""
        sub = "https://ex.org/sub/catalog.json"
        docs = {
            ROOT: catalog("./sub/catalog.json"),
            sub: {
                "type": "Catalog",
                "links": [
                    {"rel": "agents", "href": "./AGENTS.md", "type": "text/markdown"},
                ],
            },
        }
        r = crawl_catalog(ROOT, FakeFetcher(docs=docs), now=FROZEN)
        assert r["validation"]["has_agents_md"] is False

    def test_a_describedby_link_to_another_document_is_not_a_readme(self):
        """PORTO-CORE-062 names README.md, not any Markdown description.

        `describedby` is a general STAC relation, and a catalog may point it
        at a data dictionary or a summary for agents. rashid reads the href
        for the same reason (PTL-FIL-003), so reading only the relation would
        pass a catalog that rashid fails.
        """
        doc = {
            "type": "Catalog",
            "links": [
                {"rel": "describedby", "href": "./llms.txt", "type": "text/markdown"},
            ],
        }
        r = crawl_catalog(ROOT, FakeFetcher(docs={ROOT: doc}), now=FROZEN)
        assert r["validation"]["has_readme"] is False

    def test_the_readme_counts_among_other_describedby_links(self):
        """One conforming link is enough. Live catalogs publish several."""
        doc = {
            "type": "Catalog",
            "links": [
                {"rel": "describedby", "href": "./llms.txt", "type": "text/markdown"},
                {"rel": "describedby", "href": "./README.md", "type": "text/markdown"},
            ],
        }
        r = crawl_catalog(ROOT, FakeFetcher(docs={ROOT: doc}), now=FROZEN)
        assert r["validation"]["has_readme"] is True

    def test_the_document_may_sit_anywhere_and_carry_a_query(self):
        doc = {
            "type": "Catalog",
            "links": [
                {
                    "rel": "agents",
                    "href": "https://cdn.example.org/d/AGENTS.md?v=2",
                    "type": "text/markdown",
                },
                {"rel": "describedby", "href": "docs/README.md", "type": "text/markdown"},
            ],
        }
        r = crawl_catalog(ROOT, FakeFetcher(docs={ROOT: doc}), now=FROZEN)
        assert r["validation"]["has_agents_md"] is True
        assert r["validation"]["has_readme"] is True

    def test_each_flag_reads_its_own_relation(self):
        """Guards the two calls against a swapped argument."""
        doc = {
            "type": "Catalog",
            "links": [
                {"rel": "agents", "href": "./AGENTS.md", "type": "text/markdown"},
            ],
        }
        r = crawl_catalog(ROOT, FakeFetcher(docs={ROOT: doc}), now=FROZEN)
        assert r["validation"]["has_agents_md"] is True
        assert r["validation"]["has_readme"] is False


class TestProbes:
    def test_search_endpoint_marks_catalog_as_api(self):
        f = FakeFetcher(docs={ROOT: catalog()}, ok={"https://ex.org/search"})
        assert crawl_catalog(ROOT, f, now=FROZEN)["api_type"] == "api"

    def test_missing_search_endpoint_marks_static(self):
        f = FakeFetcher(docs={ROOT: catalog()})
        assert crawl_catalog(ROOT, f, now=FROZEN)["api_type"] == "static"

