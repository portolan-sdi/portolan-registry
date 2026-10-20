"""Nightly checks update statuses, send emails, and publish deprecations (#234).

The crawler reads the `nested` fixtures. Test doubles replace the validators
and email sender.
"""

from __future__ import annotations

import pytest
import revalidate_all
from registry_run import Registry

from registry.status import STALE_THRESHOLD_DAYS


@pytest.fixture
def registry(tmp_path, monkeypatch):
    return Registry(tmp_path, monkeypatch, revalidate_all)


@pytest.fixture
def mailed(monkeypatch):
    calls = []
    monkeypatch.setattr(
        revalidate_all,
        "send_stale_notification",
        lambda catalog_id, **kw: calls.append({"id": catalog_id, **kw}) or True,
    )
    return calls


def run():
    assert revalidate_all.main(["--no-notify"]) == 0


DEPRECATED = "deprecated: true\nsuperseded_by: next\n"


class TestDeprecatedCatalog:
    def test_a_validator_failure_makes_it_stale_and_mails_the_submitter(self, registry, mailed):
        registry.entry("old", DEPRECATED)
        registry.entry("next")
        registry.published("old")
        registry.published("next")
        registry.fail_validation()
        run()
        link = registry.link("old")
        assert link["portolan_registry:status"] == "stale"
        assert "rashid PTL-CNF-001" in link["portolan_registry:failure_reason"]
        assert [m["id"] for m in mailed] == ["old"]
        assert mailed[0]["deprecated"] is True
        assert mailed[0]["superseded_by"] == "next"

    def test_31_failing_nights_remove_it(self, registry, mailed):
        """Validation failures change the status to removed (issue #234, step 7)."""
        registry.entry("old", "deprecated: true\n")
        registry.published("old", status="stale", stale_days=STALE_THRESHOLD_DAYS + 1)
        registry.fail_validation()
        run()
        assert registry.link("old")["portolan_registry:status"] == "removed"
        assert mailed == []

    def test_a_still_stale_catalog_is_not_mailed_again(self, registry, mailed):
        registry.entry("old", "deprecated: true\n")
        registry.published("old", status="stale", stale_days=3)
        registry.fail_validation()
        run()
        assert registry.link("old")["portolan_registry:status"] == "stale"
        assert mailed == []

    def test_one_passing_night_resets_the_clock(self, registry, mailed):
        registry.entry("old", "deprecated: true\n")
        registry.published("old", status="stale", stale_days=20)
        run()
        link = registry.link("old")
        assert link["portolan_registry:status"] == "valid"
        assert link["portolan_registry:stale_since"] is None

    def test_publishes_the_deprecation_fields(self, registry, mailed):
        registry.entry("old", DEPRECATED + "deprecated_reason: Replaced.\n")
        registry.entry("next")
        registry.published("old")
        registry.published("next")
        run()
        link = registry.link("old")
        assert link["portolan_registry:deprecated"] is True
        assert link["portolan_registry:superseded_by"] == "next"
        assert link["portolan_registry:deprecated_reason"] == "Replaced."
        assert registry.link("next")["portolan_registry:deprecated"] is False

    def test_a_removed_catalog_keeps_its_flag_without_a_crawl(self, registry, mailed):
        registry.entry("old", "deprecated: true\n")
        registry.published("old", status="removed", stale_days=40)
        run()
        link = registry.link("old")
        assert link["portolan_registry:status"] == "removed"
        assert link["portolan_registry:deprecated"] is True


class TestNoValidatorAnswer:
    """Preserve status when the validators return no result.

    `settle_stac_valid` preserves the last published value. A previous failure
    must not count as a new failure, because the publisher may have fixed the
    catalog since that check.
    """

    def test_a_stale_catalog_keeps_its_clock(self, registry, mailed):
        registry.entry("old", "deprecated: true\n")
        registry.published("old", status="stale", stale_days=20, stac_valid=False)
        stale_since = registry.links[0]["portolan_registry:stale_since"]
        registry.validators_answer = False
        run()
        link = registry.link("old")
        assert link["portolan_registry:status"] == "stale"
        assert link["portolan_registry:stale_since"] == stale_since
        assert link["portolan_registry:stac_valid"] is False

    def test_a_catalog_past_the_threshold_is_not_removed(self, registry, mailed):
        registry.entry("old", "deprecated: true\n")
        registry.published(
            "old", status="stale", stale_days=STALE_THRESHOLD_DAYS + 1, stac_valid=False
        )
        registry.validators_answer = False
        run()
        assert registry.link("old")["portolan_registry:status"] == "stale"

    def test_a_valid_catalog_does_not_go_stale(self, registry, mailed):
        registry.entry("old", "deprecated: true\n")
        registry.published("old", stac_valid=False)
        registry.validators_answer = False
        run()
        assert registry.link("old")["portolan_registry:status"] == "valid"
        assert mailed == []


class TestCatalogThatIsNotDeprecated:
    def test_a_validator_failure_leaves_the_status_alone(self, registry, mailed):
        registry.entry("cat")
        registry.published("cat")
        registry.fail_validation()
        run()
        link = registry.link("cat")
        assert link["portolan_registry:status"] == "valid"
        assert link["portolan_registry:stac_valid"] is False
        assert mailed == []

    def test_a_catalog_that_does_not_answer_goes_stale(self, registry, mailed):
        registry.entry("cat")
        registry.published("cat")
        registry.answers = False
        run()
        assert registry.link("cat")["portolan_registry:status"] == "stale"
        assert mailed[0]["deprecated"] is False
