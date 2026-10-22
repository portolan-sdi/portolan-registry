"""Publishing preserves failed catalogs' statuses for the nightly check (#234).

Publishing runs after entry changes merge. It must preserve a deprecated
catalog's status when validation fails, so removed catalogs stay removed and
the nightly check can detect the first failure and notify the submitter.
"""

from __future__ import annotations

import json

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


class TestValidationFailureDate:
    """Publishing records failure timestamps consistently with nightly checks (#252)."""

    SINCE = "2026-01-01T00:00:00+00:00"

    def test_a_new_failure_is_dated(self, registry):
        registry.entry("cat")
        registry.published("cat", stac_valid=True)
        registry.fail_validation()
        run()
        link = registry.link("cat")
        assert link["portolan_registry:stac_valid"] is False
        assert link["portolan_registry:stac_invalid_since"] is not None

    def test_a_continued_failure_keeps_its_date(self, registry):
        registry.entry("cat")
        registry.published("cat", stac_valid=False, stac_invalid_since=self.SINCE)
        registry.fail_validation()
        run()
        assert registry.link("cat")["portolan_registry:stac_invalid_since"] == self.SINCE

    def test_a_pass_clears_the_date(self, registry):
        registry.entry("cat")
        registry.published("cat", stac_valid=False, stac_invalid_since=self.SINCE)
        run()
        assert registry.link("cat")["portolan_registry:stac_invalid_since"] is None


class TestVanishedField:
    def test_refuses_a_field_the_crawler_stopped_writing(self, registry, capsys):
        """#203: a field with a value before and none now stops the export."""
        registry.entry("a")
        registry.published("a")
        path = registry.root / "exports" / "catalogs.json"
        previous = json.loads(path.read_text())
        previous["links"][0]["portolan_registry:a_later_field"] = "a value"
        path.write_text(json.dumps(previous))
        assert publish_export.main([]) == 1
        assert "portolan_registry:a_later_field" in capsys.readouterr().err
        assert json.loads(path.read_text()) == previous
