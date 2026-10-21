"""Catalog validation status transitions.

The first failed check changes `valid` to `stale`. Another failure after
STALE_THRESHOLD_DAYS changes `stale` to `removed`. A successful check
restores `valid`.

`passes` evaluates reachable catalogs. A catalog that cannot be fetched
always fails.
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
    """Return whether a reachable catalog passes its registry check.

    Use `ValidationReport.passed` from the current run. The published
    `stac_valid` can contain a previous run's result when the validators
    return no result.

    Catalogs that are not deprecated pass if they are reachable. Validator
    errors affect status only for deprecated catalogs.

    If a deprecated catalog has no validation result, return `None`. The
    caller preserves its current status and failure date.
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
