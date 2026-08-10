"""Bbox cleaning and aggregation."""

from __future__ import annotations

import math

import pytest

from registry.bbox import clean_bbox, collection_bbox, union_bboxes


class TestCleanBbox:
    def test_valid_2d_passes_through(self):
        assert clean_bbox([-1.0, 2.0, 3.0, 4.0]) == [-1.0, 2.0, 3.0, 4.0]

    def test_valid_3d_passes_through(self):
        b = [0.0, 0.0, 10.0, 1.0, 1.0, 20.0]
        assert clean_bbox(b) == b

    @pytest.mark.parametrize("bad", [None, [], [1.0, 2.0], [1.0, 2.0, 3.0, 4.0, 5.0]])
    def test_wrong_length_rejected(self, bad):
        assert clean_bbox(bad) is None

    def test_sentinel_rejected(self):
        # The exact value seen in the IGN Argentina catalog.
        assert clean_bbox([-1.7976931348623157e308, -1.7976931348623157e308, -54.6, -21.9]) is None

    @pytest.mark.parametrize("v", [math.nan, math.inf, -math.inf])
    def test_nan_and_infinity_rejected(self, v):
        assert clean_bbox([v, 0.0, 1.0, 1.0]) is None

    def test_non_numeric_rejected(self):
        assert clean_bbox(["0", 0.0, 1.0, 1.0]) is None

    def test_booleans_rejected(self):
        # bool is an int subclass; a bbox of booleans is not a bbox.
        assert clean_bbox([True, False, True, True]) is None

    def test_latitude_overshoot_is_clamped_not_dropped(self):
        # Real data: four IGN Argentina collections sit at -90.00000001.
        assert clean_bbox([-74.0, -90.00000001, -25.0, -21.78]) == [
            -74.0, -90.0, -25.0, -21.78
        ]

    def test_longitude_overshoot_is_clamped(self):
        assert clean_bbox([-180.0000001, 0.0, 180.0000001, 1.0]) == [
            -180.0, 0.0, 180.0, 1.0
        ]

    def test_south_greater_than_north_rejected(self):
        assert clean_bbox([0.0, 10.0, 1.0, 5.0]) is None


class TestUnionBboxes:
    def test_empty_is_none(self):
        assert union_bboxes([]) is None

    def test_2d_union(self):
        assert union_bboxes([[0, 0, 1, 1], [-5, -5, 2, 2]]) == [-5, -5, 2, 2]

    def test_3d_union_keeps_altitude_and_correct_corners(self):
        a = [0.0, 0.0, 10.0, 1.0, 1.0, 20.0]
        b = [-5.0, -5.0, 0.0, 5.0, 5.0, 30.0]
        assert union_bboxes([a, b]) == [-5.0, -5.0, 0.0, 5.0, 5.0, 30.0]

    def test_3d_does_not_read_altitude_as_east(self):
        # Regression: hardcoded indices 0-3 returned east = min_alt = 10.0.
        assert union_bboxes([[0.0, 0.0, 10.0, 1.0, 1.0, 20.0]])[3] == 1.0

    def test_mixed_dimensions_degrade_to_2d(self):
        assert union_bboxes([[0, 0, 1, 1], [2, 2, 5, 3, 3, 9]]) == [0, 0, 3, 3]


class TestCollectionBbox:
    def test_reads_first_bbox_of_spatial_extent(self):
        c = {"extent": {"spatial": {"bbox": [[1.0, 2.0, 3.0, 4.0]]}}}
        assert collection_bbox(c) == [1.0, 2.0, 3.0, 4.0]

    @pytest.mark.parametrize(
        "extent",
        [
            {},
            {"spatial": {}},
            {"spatial": None},
            {"spatial": {"bbox": []}},
            {"spatial": {"bbox": None}},
            {"spatial": {"bbox": [[]]}},
            {"spatial": {"bbox": [None]}},
        ],
    )
    def test_degenerate_extents_return_none_without_raising(self, extent):
        assert collection_bbox({"extent": extent}) is None

    def test_missing_extent_returns_none(self):
        assert collection_bbox({}) is None
