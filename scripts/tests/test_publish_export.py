"""Publishing preserves failed catalogs' statuses for the nightly check (#234).

Publishing runs after entry changes merge. It must preserve a deprecated
catalog's status when validation fails, so removed catalogs stay removed and
the nightly check can detect the first failure and notify the submitter.
"""

from __future__ import annotations

import publish_export
import pytest
from registry_run import Registry


@pytest.fixture
def registry(tmp_path, monkeypatch):
    return Registry(tmp_path, monkeypatch, publish_export)


def run():
    assert publish_export.main([]) == 0


class TestDeprecatedCatalog:
    def test_a_removed_catalog_that_fails_validation_stays_removed(self, registry):
        """A failed check must not restore a removed catalog (issue #234, step 8)."""
        registry.entry("old", "deprecated: true\n")
        registry.published("old", status="removed", stale_days=40)
        registry.fail_validation()
        run()
        link = registry.link("old")
        assert link["portolan_registry:status"] == "removed"
        assert link["portolan_registry:deprecated"] is True

    def test_a_stale_catalog_keeps_its_clock(self, registry):
        registry.entry("old", "deprecated: true\n")
        registry.published("old", status="stale", stale_days=5)
        stale_since = registry.links[0]["portolan_registry:stale_since"]
        registry.fail_validation()
        run()
        link = registry.link("old")
        assert link["portolan_registry:status"] == "stale"
        assert link["portolan_registry:stale_since"] == stale_since

    def test_a_valid_catalog_that_fails_validation_stays_valid_for_the_nightly(self, registry):
        registry.entry("old", "deprecated: true\n")
        registry.published("old")
        registry.fail_validation()
        run()
        assert registry.link("old")["portolan_registry:status"] == "valid"

    def test_a_catalog_that_passes_comes_back(self, registry):
        registry.entry("old", "deprecated: true\n")
        registry.published("old", status="removed", stale_days=40)
        run()
        assert registry.link("old")["portolan_registry:status"] == "valid"

    def test_a_failed_crawl_keeps_the_flag_on_the_carried_link(self, registry):
        """Publish deprecation fields even when the crawl fails (issue #234, step 9)."""
        registry.entry("old", "deprecated: true\nsuperseded_by: next\n")
        registry.entry("next")
        registry.published("old")
        registry.published("next")
        registry.answers = False
        run()
        link = registry.link("old")
        assert link["portolan_registry:deprecated"] is True
        assert link["portolan_registry:superseded_by"] == "next"


class TestNoValidatorAnswer:
    def test_a_removed_catalog_stays_removed(self, registry):
        """Restoring a deprecated catalog requires a passing validation result."""
        registry.entry("old", "deprecated: true\n")
        registry.published("old", status="removed", stale_days=40, stac_valid=False)
        registry.validators_answer = False
        run()
        assert registry.link("old")["portolan_registry:status"] == "removed"

    def test_a_stale_catalog_is_not_reset_to_valid(self, registry):
        registry.entry("old", "deprecated: true\n")
        registry.published("old", status="stale", stale_days=5, stac_valid=True)
        registry.validators_answer = False
        run()
        assert registry.link("old")["portolan_registry:status"] == "stale"


class TestCatalogThatIsNotDeprecated:
    def test_a_removed_catalog_that_answers_comes_back(self, registry):
        """Reachable catalogs that are not deprecated can be registered again."""
        registry.entry("cat")
        registry.published("cat", status="removed", stale_days=40)
        registry.fail_validation()
        run()
        link = registry.link("cat")
        assert link["portolan_registry:status"] == "valid"
        assert link["portolan_registry:stale_since"] is None
