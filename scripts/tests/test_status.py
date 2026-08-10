"""Validation status transitions."""

from __future__ import annotations

from datetime import datetime, timezone

from registry.status import STALE_THRESHOLD_DAYS, update_status

JAN1 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def at(day: int) -> datetime:
    return datetime(2026, 1, day, tzinfo=timezone.utc)


class TestSuccess:
    def test_success_clears_everything(self):
        out = update_status(
            {"status": "stale", "stale_since": JAN1.isoformat(), "failure_reason": "x"},
            passed=True,
            now=at(5),
        )
        assert out["status"] == "valid"
        assert out["stale_since"] is None
        assert out["failure_reason"] is None

    def test_success_restores_a_removed_catalog(self):
        out = update_status({"status": "removed"}, passed=True, now=at(5))
        assert out["status"] == "valid"


class TestFailure:
    def test_first_failure_goes_stale_and_starts_the_clock(self):
        out = update_status(
            {"status": "valid"}, passed=False, failure_reason="404", now=at(5)
        )
        assert out["status"] == "stale"
        assert out["stale_since"] == at(5).isoformat()
        assert out["failure_reason"] == "404"

    def test_still_stale_before_the_threshold(self):
        out = update_status(
            {"status": "stale", "stale_since": JAN1.isoformat()},
            passed=False,
            now=at(1 + STALE_THRESHOLD_DAYS - 1),
        )
        assert out["status"] == "stale"
        assert out["stale_since"] == JAN1.isoformat()

    def test_removed_at_the_threshold(self):
        out = update_status(
            {"status": "stale", "stale_since": JAN1.isoformat()},
            passed=False,
            now=at(1 + STALE_THRESHOLD_DAYS),
        )
        assert out["status"] == "removed"
        assert "30+ days" in out["failure_reason"]

    def test_removed_stays_removed(self):
        out = update_status(
            {"status": "removed", "stale_since": JAN1.isoformat(), "failure_reason": "gone"},
            passed=False,
            now=at(5),
        )
        assert out["status"] == "removed"
        assert out["failure_reason"] == "gone"


class TestMalformedState:
    def test_stale_without_stale_since_does_not_raise(self):
        """Regression: this raised AttributeError from inside the caller's
        except block, failing the step so the commit never ran and every
        catalog's state froze."""
        out = update_status(
            {"status": "stale", "stale_since": None}, passed=False, now=at(5)
        )
        assert out["status"] == "stale"
        assert out["stale_since"] == at(5).isoformat()

    def test_stale_with_unparseable_stale_since_restarts_the_clock(self):
        out = update_status(
            {"status": "stale", "stale_since": "not a date"}, passed=False, now=at(5)
        )
        assert out["status"] == "stale"
        assert out["stale_since"] == at(5).isoformat()

    def test_zulu_suffix_is_parsed(self):
        out = update_status(
            {"status": "stale", "stale_since": "2026-01-01T00:00:00Z"},
            passed=False,
            now=at(1 + STALE_THRESHOLD_DAYS),
        )
        assert out["status"] == "removed"

    def test_empty_state_is_treated_as_valid(self):
        out = update_status({}, passed=False, now=at(5))
        assert out["status"] == "stale"
