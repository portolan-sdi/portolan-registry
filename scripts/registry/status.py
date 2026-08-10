"""Catalog validation status transitions.

valid -> stale on the first failed re-validation, stale -> removed once it
has been stale for STALE_THRESHOLD_DAYS. A successful crawl always restores
valid, which is how a re-submitted catalog comes back.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta, timezone

STALE_THRESHOLD_DAYS = 30


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def update_status(
    current_state: Mapping,
    *,
    passed: bool,
    failure_reason: str | None = None,
    now: datetime | None = None,
) -> dict:
    """Return the next state for one catalog.

    `now` is injected so the 30-day rule is testable without freezing wall
    time.
    """
    now = now or datetime.now(timezone.utc)
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
        stale_dt = _parse(stale_since)
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
