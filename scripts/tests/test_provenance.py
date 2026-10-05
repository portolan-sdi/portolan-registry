"""Producer, processor, host, and official-versus-mirror, from STAC providers.

portolan-spec core.md, Source Provenance: a catalog is official when its
producer and host are the same organization, and a mirror when they differ.
The registry derives the kind from each collection's `providers`. No catalog
declares it.
"""

from __future__ import annotations

from registry.crawl import CollectionSummary
from registry.provenance import catalog_provenance, collection_parties

CARTO = {"name": "CARTO", "roles": ["processor", "host"], "url": "https://carto.com"}


def summary(providers: list | None) -> CollectionSummary:
    s = CollectionSummary(id="c", url="https://ex.org/c/collection.json")
    s.producers, s.processors, s.host = collection_parties(providers)
    return s


class TestCollectionParties:
    def test_splits_providers_by_role_and_drops_the_description(self):
        producers, processors, host = collection_parties(
            [
                {
                    "name": "Comune di Bologna",
                    "roles": ["producer", "licensor"],
                    "url": "https://opendata.comune.bologna.it",
                    "description": "Two sentences that the export drops.",
                },
                CARTO,
            ]
        )
        assert producers == [
            {"name": "Comune di Bologna", "url": "https://opendata.comune.bologna.it"}
        ]
        assert processors == [{"name": "CARTO", "url": "https://carto.com"}]
        assert host == {"name": "CARTO", "url": "https://carto.com"}

    def test_url_is_absent_when_the_provider_gives_none(self):
        producers, _, _ = collection_parties([{"name": "TriMet", "roles": ["producer"]}])
        assert producers == [{"name": "TriMet"}]

    def test_the_last_host_wins(self):
        """The spec puts exactly one host last in the list."""
        _, _, host = collection_parties(
            [
                {"name": "A", "roles": ["host"]},
                {"name": "B", "roles": ["host"]},
            ]
        )
        assert host == {"name": "B"}

    def test_a_provider_without_a_name_is_ignored(self):
        producers, _, host = collection_parties(
            [{"roles": ["producer"]}, {"name": "  ", "roles": ["host"]}, "junk"]
        )
        assert producers == []
        assert host is None

    def test_no_providers_names_nobody(self):
        assert collection_parties(None) == ([], [], None)


class TestKind:
    def test_a_producer_that_is_also_the_host_is_official(self):
        p = catalog_provenance(
            [summary([{"name": "Taylor Geospatial", "roles": ["producer", "host"]}])]
        )
        assert p["kind"] == "official"

    def test_a_host_that_is_one_of_several_producers_is_official(self):
        p = catalog_provenance(
            [
                summary(
                    [
                        {"name": "Arizona State University", "roles": ["producer"]},
                        {"name": "World Resources Institute", "roles": ["producer"]},
                        {"name": "World Resources Institute", "roles": ["host"]},
                    ]
                )
            ]
        )
        assert p["kind"] == "official"

    def test_names_compare_without_case_or_extra_whitespace(self):
        p = catalog_provenance(
            [
                summary(
                    [
                        {"name": "Planet Labs  PBC", "roles": ["producer"]},
                        {"name": "planet labs pbc", "roles": ["host"]},
                    ]
                )
            ]
        )
        assert p["kind"] == "official"

    def test_a_host_that_produced_nothing_is_a_mirror(self):
        p = catalog_provenance(
            [summary([{"name": "Comune di Bologna", "roles": ["producer"]}, CARTO])]
        )
        assert p["kind"] == "mirror"

    def test_one_mirrored_collection_makes_the_catalog_a_mirror(self):
        """An organization that hosts data it did not produce is a mirror
        under the spec, even when it also publishes data of its own."""
        own = summary([{"name": "CARTO", "roles": ["producer", "host"]}])
        mirrored = summary([{"name": "OpenCellID", "roles": ["producer"]}, CARTO])
        assert catalog_provenance([own, mirrored, own])["kind"] == "mirror"

    def test_null_when_no_collection_names_a_producer(self):
        p = catalog_provenance([summary([{"name": "TODO: Add value", "roles": ["host"]}])])
        assert p["kind"] is None

    def test_null_when_no_collection_names_a_host(self):
        p = catalog_provenance([summary([{"name": "TriMet", "roles": ["producer"]}])])
        assert p["kind"] is None

    def test_null_when_no_collection_declares_providers(self):
        assert catalog_provenance([summary(None), summary([])])["kind"] is None

    def test_a_collection_that_names_no_host_does_not_decide(self):
        p = catalog_provenance(
            [
                summary(None),
                summary([{"name": "Comune di Bologna", "roles": ["producer"]}, CARTO]),
            ]
        )
        assert p["kind"] == "mirror"

    def test_an_empty_catalog_is_null(self):
        assert catalog_provenance([]) == {
            "kind": None,
            "producers": [],
            "processors": [],
            "host": None,
        }


class TestParties:
    def test_a_repeated_host_is_named_once(self):
        p = catalog_provenance(
            [summary([{"name": "Comune di Bologna", "roles": ["producer"]}, CARTO])] * 30
        )
        assert p["host"] == {"name": "CARTO", "url": "https://carto.com"}
        assert p["producers"] == [{"name": "Comune di Bologna"}]
        assert p["processors"] == [{"name": "CARTO", "url": "https://carto.com"}]

    def test_two_agencies_on_one_host_both_reach_the_list(self):
        p = catalog_provenance(
            [
                summary(
                    [
                        {"name": "MAPA", "roles": ["producer"], "url": "https://www.gov.br/agricultura"},
                        {"name": "ANP", "roles": ["producer"], "url": "https://www.gov.br/anp"},
                        {"name": "World Resources Institute", "roles": ["host"]},
                    ]
                )
            ]
        )
        assert p["producers"] == [
            {"name": "MAPA", "url": "https://www.gov.br/agricultura"},
            {"name": "ANP", "url": "https://www.gov.br/anp"},
        ]

    def test_producers_keep_the_order_the_catalog_names_them(self):
        p = catalog_provenance(
            [
                summary([{"name": "B", "roles": ["producer"]}, CARTO]),
                summary([{"name": "A", "roles": ["producer"]}, CARTO]),
                summary([{"name": "B", "roles": ["producer"]}, CARTO]),
            ]
        )
        assert [x["name"] for x in p["producers"]] == ["B", "A"]

    def test_one_producer_with_many_dataset_urls_is_one_party(self):
        """CARTO mirrors link each collection's producer to that dataset's
        own page. The organization is the same, so the first URL stands."""
        p = catalog_provenance(
            [
                summary([{"name": "Ajuntament de Barcelona", "roles": ["producer"], "url": "https://ex.org/1"}, CARTO]),
                summary([{"name": "Ajuntament de Barcelona", "roles": ["producer"], "url": "https://ex.org/2"}, CARTO]),
            ]
        )
        assert p["producers"] == [
            {"name": "Ajuntament de Barcelona", "url": "https://ex.org/1"}
        ]

    def test_a_later_url_fills_a_party_that_had_none(self):
        p = catalog_provenance(
            [
                summary([{"name": "TriMet", "roles": ["producer"]}]),
                summary([{"name": "TriMet", "roles": ["producer"], "url": "https://trimet.org"}]),
            ]
        )
        assert p["producers"] == [{"name": "TriMet", "url": "https://trimet.org"}]

    def test_the_host_most_collections_name_is_the_host(self):
        source = {"name": "Source Cooperative", "roles": ["host"], "url": "https://source.coop"}
        other = {"name": "Portolan / Source Cooperative", "roles": ["host"]}
        producer = {"name": "Rijkswaterstaat", "roles": ["producer"]}
        p = catalog_provenance(
            [summary([producer, other])] + [summary([producer, source])] * 3
        )
        assert p["host"] == {"name": "Source Cooperative", "url": "https://source.coop"}

    def test_a_tie_between_hosts_goes_to_the_first_named(self):
        producer = {"name": "P", "roles": ["producer"]}
        p = catalog_provenance(
            [
                summary([producer, {"name": "First", "roles": ["host"]}]),
                summary([producer, {"name": "Second", "roles": ["host"]}]),
            ]
        )
        assert p["host"] == {"name": "First"}
