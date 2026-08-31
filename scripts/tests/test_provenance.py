"""Deriving official-vs-mirror, and naming the parties behind a catalog.

The cases here are drawn from the registered catalogs. Each docstring names
the catalog whose providers the case reproduces, so a reader can go and look.
"""

from __future__ import annotations

from registry.provenance import catalog_kind, collection_kind, parties


class TestCollectionKind:
    def test_one_provider_holding_both_roles_is_official(self):
        """bot-open-data: one organization produces, licenses, and hosts."""
        assert (
            collection_kind(
                [
                    {
                        "name": "Back-on-Track Europe AISBL",
                        "roles": ["producer", "licensor", "host"],
                        "url": "https://back-on-track.eu",
                    }
                ]
            )
            == "official"
        )

    def test_a_different_host_is_a_mirror(self):
        """st-louis-open-data-mirror: the city produces, TGE Labs serves."""
        assert (
            collection_kind(
                [
                    {
                        "name": "City of St. Louis",
                        "roles": ["producer", "licensor"],
                        "url": "https://www.stlouis-mo.gov/data/",
                    },
                    {
                        "name": "TGE Labs",
                        "roles": ["host"],
                        "url": "https://github.com/cholmes/portolan-catalog-stlouis",
                    },
                ]
            )
            == "mirror"
        )

    def test_one_organization_under_two_names_is_official(self):
        """planet-disasterdata writes the producer and the host differently.

        Both sit on planet.com. Comparing the names alone reports a mirror,
        which is what made this case worth a test.
        """
        assert (
            collection_kind(
                [
                    {
                        "name": "Planet Labs PBC",
                        "roles": ["producer", "processor", "licensor"],
                        "url": "https://www.planet.com/",
                    },
                    {
                        "name": "Planet Crisis Response Program",
                        "roles": ["host"],
                        "url": "https://www.planet.com/disasterdata/",
                    },
                ]
            )
            == "official"
        )

    def test_a_shared_public_suffix_is_not_a_shared_organization(self):
        # cadastral: a Brazilian agency produces, Source Cooperative serves.
        # Comparing registrable domains would read gov.br and source.coop as
        # co.uk-style suffixes and match nothing; comparing hosts is what
        # keeps these two apart.
        assert (
            collection_kind(
                [
                    {
                        "name": "Serviço Florestal Brasileiro",
                        "roles": ["producer"],
                        "url": "https://www.gov.br/florestal/",
                    },
                    {
                        "name": "Tristan Grupp",
                        "roles": ["processor", "host"],
                        "url": "https://source.coop/tristangruppwri/cadastral",
                    },
                ]
            )
            == "mirror"
        )

    def test_two_agencies_on_one_government_host_read_as_one(self):
        # A documented limitation, not a goal. One organization per host is an
        # assumption, and gov.br gives every agency a path on the same host.
        # No registered catalog declares its producer and its host this way.
        assert (
            collection_kind(
                [
                    {
                        "name": "Serviço Florestal Brasileiro",
                        "roles": ["producer"],
                        "url": "https://www.gov.br/florestal/",
                    },
                    {
                        "name": "Instituto Nacional de Colonização",
                        "roles": ["host"],
                        "url": "https://www.gov.br/incra/",
                    },
                ]
            )
            == "official"
        )

    def test_a_matching_name_stands_in_for_a_missing_url(self):
        # The specification lets a producer declare a name and no URL.
        assert (
            collection_kind(
                [
                    {"name": "Studio Bereikbaar", "roles": ["producer"]},
                    {
                        "name": "Studio Bereikbaar",
                        "roles": ["host"],
                        "url": "https://www.studiobereikbaar.nl",
                    },
                ]
            )
            == "official"
        )

    def test_a_host_among_several_producers_is_official(self):
        """carto-do spatial-features: CARTO produces with others and hosts."""
        assert (
            collection_kind(
                [
                    {"name": "CARTO", "roles": ["producer"], "url": "https://carto.com"},
                    {
                        "name": "WorldPop",
                        "roles": ["producer"],
                        "url": "https://www.worldpop.org",
                    },
                    {"name": "CARTO", "roles": ["host"], "url": "https://carto.com"},
                ]
            )
            == "official"
        )

    def test_a_processor_is_not_a_producer(self):
        # catalog-1781203130384 converts someone else's data and hosts it.
        # Processing is not producing, so this stays a mirror.
        assert (
            collection_kind(
                [
                    {
                        "name": "Instituto Geográfico Nacional",
                        "roles": ["producer"],
                        "url": "https://www.ign.gob.ar/",
                    },
                    {
                        "name": "Nissim Lebovits",
                        "roles": ["processor"],
                        "url": "https://radiant.earth/",
                    },
                    {
                        "name": "Source Cooperative",
                        "roles": ["host"],
                        "url": "https://source.coop/",
                    },
                ]
            )
            == "mirror"
        )

    def test_no_host_says_nothing(self):
        assert collection_kind([{"name": "Someone", "roles": ["producer"]}]) is None

    def test_no_producer_says_nothing(self):
        """The `catalog` entry declares a host and a placeholder name."""
        assert (
            collection_kind(
                [{"name": "TODO: Add value", "roles": ["host"], "email": "TODO"}]
            )
            is None
        )

    def test_absent_providers_say_nothing(self):
        """jrc-glofas declares none at all."""
        assert collection_kind(None) is None
        assert collection_kind([]) is None

    def test_survives_providers_that_are_not_a_list_of_objects(self):
        # The registry reads catalogs it does not control.
        assert collection_kind("Acme Corp") is None
        assert collection_kind(["Acme Corp", None, 7]) is None
        assert collection_kind([{"name": "Acme", "roles": "producer"}]) is None

    def test_an_unnamed_anonymous_provider_says_nothing(self):
        assert collection_kind([{"roles": ["producer"]}, {"roles": ["host"]}]) is None


class TestCatalogKind:
    def test_every_collection_official_is_official(self):
        assert catalog_kind(["official", "official"]) == "official"

    def test_every_collection_mirrored_is_a_mirror(self):
        assert catalog_kind(["mirror", "mirror"]) == "mirror"

    def test_one_mirrored_collection_makes_the_catalog_a_mirror(self):
        """carto-do produces six collections and re-hosts three.

        "official" claims the catalog is the canonical home of everything
        beneath it, and that is false here.
        """
        assert catalog_kind(["official", "official", "mirror"]) == "mirror"

    def test_collections_that_say_nothing_are_ignored(self):
        assert catalog_kind([None, "official", None]) == "official"

    def test_a_catalog_that_says_nothing_says_nothing(self):
        assert catalog_kind([]) is None
        assert catalog_kind([None, None]) is None


class TestParties:
    def test_names_the_producers_and_the_host(self):
        producers, host = parties(
            [
                [
                    {
                        "name": "TriMet",
                        "roles": ["producer", "licensor"],
                        "url": "https://developer.trimet.org/gis/",
                    },
                    {
                        "name": "Chris Holmes",
                        "roles": ["host"],
                        "url": "https://github.com/cholmes/portolan-catalog-trimet",
                    },
                ]
            ]
        )
        assert producers == [
            {"name": "TriMet", "url": "https://developer.trimet.org/gis/"}
        ]
        assert host == {
            "name": "Chris Holmes",
            "url": "https://github.com/cholmes/portolan-catalog-trimet",
        }

    def test_drops_the_provider_description(self):
        # ghsl gives its providers two-sentence descriptions. The export
        # carries what names a party, not the catalog's prose.
        producers, _ = parties(
            [
                [
                    {
                        "name": "European Commission",
                        "description": "Produced GHS-POP R2023A.",
                        "roles": ["producer"],
                        "url": "https://human-settlement.emergency.copernicus.eu/",
                    }
                ]
            ]
        )
        assert producers == [
            {
                "name": "European Commission",
                "url": "https://human-settlement.emergency.copernicus.eu/",
            }
        ]

    def test_keeps_the_order_the_catalog_names_them_in(self):
        producers, _ = parties(
            [
                [{"name": "First", "roles": ["producer"]}],
                [{"name": "Second", "roles": ["producer"]}],
                [{"name": "Third", "roles": ["producer"]}],
            ]
        )
        assert [p["name"] for p in producers] == ["First", "Second", "Third"]

    def test_names_one_producer_once_across_many_collections(self):
        """portolan-nl repeats Source Cooperative on every collection."""
        one = [
            {"name": "Het Kadaster", "roles": ["producer"], "url": "https://kadaster.nl"}
        ]
        producers, _ = parties([one, one, one])
        assert producers == [{"name": "Het Kadaster", "url": "https://kadaster.nl"}]

    def test_credits_two_agencies_that_share_a_host(self):
        """soft-commodity-infrastructure credits MAPA and ANP, both on gov.br.

        Matching on the host would drop ANP from the list of who made the
        data, so repeats are judged on the name alone.
        """
        producers, _ = parties(
            [
                [
                    {
                        "name": "MAPA",
                        "roles": ["producer"],
                        "url": "https://www.gov.br/agricultura/",
                    },
                    {
                        "name": "ANP",
                        "roles": ["producer"],
                        "url": "https://www.gov.br/anp/",
                    },
                ]
            ]
        )
        assert [p["name"] for p in producers] == ["MAPA", "ANP"]

    def test_omits_a_url_the_catalog_does_not_declare(self):
        producers, _ = parties([[{"name": "CONAB", "roles": ["producer"]}]])
        assert producers == [{"name": "CONAB"}]

    def test_takes_the_first_host_it_reads(self):
        producers, host = parties(
            [
                [{"name": "Root Host", "roles": ["host"]}],
                [{"name": "Collection Host", "roles": ["host"]}],
            ]
        )
        assert producers == []
        assert host == {"name": "Root Host"}

    def test_reports_nothing_when_no_provider_is_named(self):
        assert parties([]) == ([], None)
        assert parties([None, [], "nonsense"]) == ([], None)
