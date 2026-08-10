"""Bounding box cleaning and aggregation.

Portolan requires every bbox to be WGS84, 4 or 6 elements, free of NaN,
infinity, and "effectively infinite" sentinels such as +/-1.79e308, with
south <= north. Registered catalogs violate this in practice, so the registry
cleans what it reads before aggregating.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

# Anything at or beyond this magnitude is an uninitialised float, not a
# coordinate. sys.float_info.max (~1.797e308) is the value seen in the wild.
BBOX_SENTINEL = 1e300

_LON_LIMIT = 180.0
_LAT_LIMIT = 90.0


def clean_bbox(bbox: Sequence[float] | None) -> list[float] | None:
    """Return a valid WGS84 bbox, or None if it cannot be salvaged.

    Error values (NaN, infinity, sentinels) disqualify the box outright.
    Honest floating-point overshoot, such as latitude -90.00000001, is
    clamped rather than discarded.
    """
    if not bbox or len(bbox) not in (4, 6):
        return None
    if not all(
        isinstance(v, (int, float)) and not isinstance(v, bool) for v in bbox
    ):
        return None
    if any(
        math.isnan(v) or math.isinf(v) or abs(v) > BBOX_SENTINEL for v in bbox
    ):
        return None

    half = len(bbox) // 2
    mins, maxs = list(bbox[:half]), list(bbox[half:])
    for axis, limit in ((0, _LON_LIMIT), (1, _LAT_LIMIT)):
        mins[axis] = max(-limit, min(limit, mins[axis]))
        maxs[axis] = max(-limit, min(limit, maxs[axis]))
    if mins[1] > maxs[1]:
        return None
    return mins + maxs


def collection_bbox(collection: Mapping) -> list[float] | None:
    """Read the overall bbox out of a STAC Collection's spatial extent.

    Tolerates a missing, null, or empty value at every level. A collection
    with an unusable extent still counts toward the catalog; only its
    contribution to the union is dropped.
    """
    extent = collection.get("extent") or {}
    spatial = extent.get("spatial") or {}
    bboxes = spatial.get("bbox") or []
    if not bboxes:
        return None
    return clean_bbox(bboxes[0])


def union_bboxes(bboxes: Sequence[Sequence[float]]) -> list[float] | None:
    """Union cleaned bboxes.

    Splits each box by length rather than hardcoding indices. A 6-element
    bbox is ordered [west, south, min_alt, east, north, max_alt], so reading
    index 2 as east would return max altitude in the longitude slot. A set
    mixing 2D and 3D degrades to 2D.
    """
    if not bboxes:
        return None
    half = min(len(b) // 2 for b in bboxes)
    mins = [min(b[i] for b in bboxes) for i in range(half)]
    maxs = [max(b[len(b) // 2 + i] for b in bboxes) for i in range(half)]
    return mins + maxs
