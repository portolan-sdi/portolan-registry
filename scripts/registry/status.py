"""Catalog validation status transitions.

valid -> stale on the first failed re-validation, stale -> removed once it
has been stale for STALE_THRESHOLD_DAYS. A successful re-validation always
restores valid, which is how a re-submitted catalog comes back.

`passes` decides what success means for a catalog that answered. A catalog
that does not answer always fails.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

STALE_THRESHOLD_DAYS = 30


def parse_timestamp(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def passes(*, deprecated: bool, stac_valid: bool | None) -> bool | None:
    """Whether a catalog that answered passes re-validation.

    Pass this run's validator answer (`ValidationReport.passed`), not the
    published `stac_valid`. On a run with no answer, `settle_stac_valid`
    copies the last published value back, and that value is not news.

    A catalog that is not deprecated passes when it answers. The validators
    publish `stac_valid` and do not change its status, because most
    registered catalogs predate them.

    A deprecated catalog also fails when the validators report an error
    (#234). So a deprecated catalog that stays broken leaves the registry
    after STALE_THRESHOLD_DAYS. When the validators give no answer, return
    None: the run has no verdict. The caller keeps the state as it stands, so
    a run with no answer neither advances nor resets the stale clock.
    """
    if not deprecated:
        return True
    return stac_valid


def update_status(
    current_state: Mapping[str, Any],
    *,
    passed: bool,
    failure_reason: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return the next state for one catalog.

    `now` is injected so the 30-day rule is testable without freezing wall
    time.
    """
    now = now or datetime.now(UTC)
    stamp = now.isoformat()
    status = current_state.get("status", "valid")
    stale_since = current_state.get("stale_since")

    if passed:
        return {
            "status": "valid",
            "last_validated": stamp,
            "stale_since": None,
            "failure_reason": None,
        }

    if status == "valid":
        return {
            "status": "stale",
            "last_validated": stamp,
            "stale_since": stamp,
            "failure_reason": failure_reason,
        }

    if status == "stale":
        # A stale catalog with no stale_since used to raise AttributeError
        # here. That raise escaped the caller's except block, failed the
        # step, and skipped the commit -- freezing every catalog's state
        # with no error anyone would notice. Treat the clock as starting now.
        stale_dt = parse_timestamp(stale_since)
        if stale_dt is None:
            return {
                "status": "stale",
                "last_validated": stamp,
                "stale_since": stamp,
                "failure_reason": failure_reason,
            }
        if now - stale_dt >= timedelta(days=STALE_THRESHOLD_DAYS):
            return {
                "status": "removed",
                "last_validated": stamp,
                "stale_since": stale_since,
                "failure_reason": f"Stale for {STALE_THRESHOLD_DAYS}+ days",
            }
        return {
            "status": "stale",
            "last_validated": stamp,
            "stale_since": stale_since,
            "failure_reason": failure_reason,
        }

    return {
        "status": "removed",
        "last_validated": stamp,
        "stale_since": current_state.get("stale_since"),
        "failure_reason": current_state.get("failure_reason"),
    }
