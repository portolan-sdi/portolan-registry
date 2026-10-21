"""Nightly checks update statuses, send emails, and publish deprecations (#234).

Checks also record validation failures and send weekly emails (#252).

The crawler reads the `nested` fixtures. Test doubles replace the validators
and email sender.
"""

from __future__ import annotations

from datetime import UTC, datetime

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


@pytest.fixture
def flagged(monkeypatch):
    """Capture validation emails as (address, catalog IDs) pairs."""
    calls = []
    monkeypatch.setattr(
        revalidate_all,
        "send_validation_failure_notification",
        lambda email, catalogs, **kw: (
            calls.append((email, [c.catalog_id for c in catalogs])) or True
        ),
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


class TestValidationFailureFlag:
    """Record failure timestamps and send weekly validation emails (#252)."""

    @pytest.fixture
    def digest_day(self, monkeypatch):
        """Set the email schedule to include or exclude the current day."""

        def set_to(on: bool):
            today = datetime.now(UTC).weekday()
            monkeypatch.setattr(revalidate_all, "DIGEST_WEEKDAY", today if on else (today + 1) % 7)

        set_to(True)
        return set_to

    def test_a_new_failure_is_dated_and_logged(self, registry, mailed, flagged, capsys):
        registry.entry("cat")
        registry.published("cat", stac_valid=True)
        registry.fail_validation()
        run()
        link = registry.link("cat")
        assert link["portolan_registry:status"] == "valid"
        assert link["portolan_registry:stac_valid"] is False
        assert link["portolan_registry:stac_invalid_since"] is not None
        log = capsys.readouterr().err
        assert "New validation failures: 1" in log
        assert "cat: rashid PTL-CNF-001" in log
        assert mailed == []

    def test_the_email_repeats_each_digest_day_while_it_fails(
        self, registry, mailed, flagged, digest_day
    ):
        registry.entry("cat")
        registry.published("cat", stac_valid=True)
        registry.fail_validation()
        run()
        since = registry.link("cat")["portolan_registry:stac_invalid_since"]
        run()
        assert registry.link("cat")["portolan_registry:stac_invalid_since"] == since
        assert flagged == [("submitter@example.com", ["cat"])] * 2

    def test_no_email_on_other_days(self, registry, mailed, flagged, digest_day, capsys):
        digest_day(False)
        registry.entry("cat")
        registry.published("cat", stac_valid=True)
        registry.fail_validation()
        run()
        assert flagged == []
        assert "New validation failures: 1" in capsys.readouterr().err

    def test_a_catalog_that_already_fails_is_dated_when_this_ships(
        self, registry, mailed, flagged, digest_day
    ):
        """Older exports can record `stac_valid: false` without a failure timestamp."""
        registry.entry("cat")
        registry.published("cat", stac_valid=False)
        registry.fail_validation()
        run()
        assert registry.link("cat")["portolan_registry:stac_invalid_since"] is not None
        assert flagged == [("submitter@example.com", ["cat"])]

    def test_a_pass_clears_the_date_and_stops_the_email(
        self, registry, mailed, flagged, digest_day
    ):
        registry.entry("cat")
        registry.published("cat", stac_valid=False, stac_invalid_since="2026-01-01T00:00:00+00:00")
        registry.fail_validation()
        run()
        registry.pass_validation()
        run()
        assert registry.link("cat")["portolan_registry:stac_invalid_since"] is None
        assert flagged == [("submitter@example.com", ["cat"])]

    def test_a_carried_failure_is_not_emailed(self, registry, mailed, flagged, digest_day):
        """Use the current validation result for emails, even when the export retains an earlier result."""
        registry.entry("cat")
        registry.published("cat", stac_valid=False)
        registry.validators_answer = False
        run()
        link = registry.link("cat")
        assert link["portolan_registry:stac_valid"] is False
        assert link["portolan_registry:stac_invalid_since"] is None
        assert flagged == []

    def test_one_email_for_each_submitter(self, registry, mailed, flagged, digest_day):
        """Group catalogs by submitter when several fail validation in the same run."""
        for catalog_id, submitter in [
            ("a", "one@ex.org"),
            ("b", "one@ex.org"),
            ("c", "two@ex.org"),
        ]:
            registry.entry(catalog_id, submitter=submitter)
            registry.published(catalog_id, stac_valid=True)
        registry.fail_validation()
        run()
        assert sorted(flagged) == [("one@ex.org", ["a", "b"]), ("two@ex.org", ["c"])]

    def test_a_deprecated_catalog_gets_the_stale_email_alone(
        self, registry, mailed, flagged, digest_day
    ):
        registry.entry("old", "deprecated: true\n")
        registry.published("old", stac_valid=True)
        registry.fail_validation()
        run()
        link = registry.link("old")
        assert link["portolan_registry:status"] == "stale"
        assert link["portolan_registry:stac_invalid_since"] is not None
        assert [m["id"] for m in mailed] == ["old"]
        assert flagged == []

    def test_an_unreachable_catalog_keeps_its_date(self, registry, mailed, flagged, digest_day):
        since = "2026-01-01T00:00:00+00:00"
        registry.entry("cat")
        registry.published("cat", stac_valid=False, stac_invalid_since=since)
        registry.answers = False
        run()
        link = registry.link("cat")
        assert link["portolan_registry:status"] == "stale"
        assert link["portolan_registry:stac_invalid_since"] == since
        assert flagged == []
