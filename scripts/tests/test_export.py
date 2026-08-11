"""Export assembly, the golden comparison, and the no-data-loss guard."""

from __future__ import annotations

import json
import math
from datetime import timedelta

import pytest

from conftest import FIXTURES, FROZEN
from registry.crawl import crawl_catalog
from registry.export import (
    TIMESTAMP_REFRESH_DAYS,
    ExportRefused,
    build_export,
    check_export_safe,
    child_link,
    export_changed,
    load_links,
    load_state,
)

ROOT = "https://ex.org/catalog.json"
EXPORT = FIXTURES.parent.parent.parent / "exports" / "catalogs.json"


class TestChildLink:
    def test_carries_plain_bbox(self):
        link = child_link(
            {"id": "x", "url": ROOT, "bbox": [-1.0, -2.0, 3.0, 4.0]}
        )
        assert link["bbox"] == [-1.0, -2.0, 3.0, 4.0]
        assert "portolan_registry:bbox" not in link

    def test_omits_bbox_when_unknown(self):
        assert "bbox" not in child_link({"id": "x", "url": ROOT})

    def test_omits_bbox_when_none(self):
        assert "bbox" not in child_link({"id": "x", "url": ROOT, "bbox": None})

    def test_publishes_null_counts_rather_than_zero(self):
        # An uncrawled catalog measured nothing. Zero would claim it holds no
        # items and no bytes, which is a stronger and different claim.
        link = child_link({"id": "x", "url": ROOT})
        assert link["portolan_registry:item_count"] is None
        assert link["portolan_registry:total_size_bytes"] is None
        assert link["portolan_registry:counts_partial"] is False

    def test_carries_a_measured_zero_through(self):
        # A fully enumerable catalog that really holds no items keeps its zero.
        link = child_link(
            {"id": "x", "url": ROOT, "item_count": 0, "total_size_bytes": 0}
        )
        assert link["portolan_registry:item_count"] == 0
        assert link["portolan_registry:total_size_bytes"] == 0

    def test_carries_the_partial_flag(self):
        link = child_link({"id": "x", "url": ROOT, "counts_partial": True})
        assert link["portolan_registry:counts_partial"] is True

    def test_falls_back_to_id_for_title(self):
        assert child_link({"id": "x", "url": ROOT})["title"] == "x"

    def test_registry_metadata_is_prefixed(self):
        link = child_link({"id": "x", "url": ROOT, "collection_count": 7})
        assert link["portolan_registry:id"] == "x"
        assert link["portolan_registry:collection_count"] == 7

    def test_no_field_claims_the_specification_prefix(self):
        """Only the specification may name a field `portolan:`. Everything the
        registry adds on its own says so."""
        link = child_link({"id": "x", "url": ROOT})
        assert not [k for k in link if k.startswith("portolan:")]

    def test_carries_the_version_and_dates(self):
        link = child_link(
            {
                "id": "x",
                "url": ROOT,
                "spec_version": "0.1.0",
                "spec_version_mixed": True,
                "stac_version": "1.1.0",
                "updated": "2026-08-02T18:02:50Z",
                "first_registered": "2026-06-09T14:38:00+00:00",
            }
        )
        assert link["portolan_registry:spec_version"] == "0.1.0"
        assert link["portolan_registry:spec_version_mixed"] is True
        assert link["portolan_registry:stac_version"] == "1.1.0"
        assert link["portolan_registry:updated"] == "2026-08-02T18:02:50Z"
        assert link["portolan_registry:first_registered"] == "2026-06-09T14:38:00+00:00"

    def test_an_undeclared_catalog_reports_nulls(self):
        link = child_link({"id": "x", "url": ROOT})
        assert link["portolan_registry:spec_version"] is None
        assert link["portolan_registry:spec_version_mixed"] is False
        assert link["portolan_registry:updated"] is None
        assert link["portolan_registry:first_registered"] is None


class TestBuildExport:
    def test_has_root_and_self_links_before_children(self):
        e = build_export([{"id": "a", "url": ROOT}], now=FROZEN)
        assert [link["rel"] for link in e["links"]] == ["root", "self", "child"]

    def test_count_matches_child_links(self):
        e = build_export(
            [{"id": "a", "url": ROOT}, {"id": "b", "url": ROOT}], now=FROZEN
        )
        assert e["count"] == 2

    def test_children_are_sorted_by_id(self):
        e = build_export(
            [{"id": "z", "url": ROOT}, {"id": "a", "url": ROOT}], now=FROZEN
        )
        ids = [
            link["portolan_registry:id"]
            for link in e["links"]
            if link["rel"] == "child"
        ]
        assert ids == ["a", "z"]

    def test_carried_links_are_counted_and_sorted_in(self):
        e = build_export(
            [{"id": "b", "url": ROOT}],
            now=FROZEN,
            extra_links=[{"rel": "child", "portolan_registry:id": "a", "href": ROOT}],
        )
        ids = [
            link["portolan_registry:id"]
            for link in e["links"]
            if link["rel"] == "child"
        ]
        assert ids == ["a", "b"]
        assert e["count"] == 2

    def test_is_a_stac_catalog(self):
        e = build_export([], now=FROZEN)
        assert e["type"] == "Catalog"
        assert e["stac_version"] == "1.1.0"
        assert e["id"] == "portolan-registry"


class TestGolden:
    def test_export_matches_the_golden(self, tree):
        """The golden pins every field the export publishes for one catalog,
        including the license mix and the declared specification version, so
        any change to the shape of a child link has to be made on purpose."""
        result = crawl_catalog(ROOT, tree, now=FROZEN)
        result.update(
            id="example",
            status="valid",
            last_validated=FROZEN.isoformat(),
            stale_since=None,
            failure_reason=None,
        )
        export = build_export([result], now=FROZEN)
        golden = json.loads((FIXTURES / "golden_export.json").read_text())
        assert export == golden


class TestLegacyPrefix:
    """An export written before the rename still holds the only copy of its
    validation state, so it has to be readable across the cutover."""

    @staticmethod
    def written(tmp_path, link):
        path = tmp_path / "catalogs.json"
        path.write_text(json.dumps({"links": [{"rel": "child", **link}]}))
        return path

    def test_reads_state_written_under_the_old_prefix(self, tmp_path):
        path = self.written(
            tmp_path,
            {
                "portolan:id": "a",
                "portolan:status": "stale",
                "portolan:stale_since": "2026-01-01T00:00:00+00:00",
                "portolan:failure_reason": "timeout",
            },
        )
        assert load_state(path)["a"] == {
            "status": "stale",
            "last_validated": None,
            "stale_since": "2026-01-01T00:00:00+00:00",
            "failure_reason": "timeout",
        }

    def test_republishes_a_carried_link_under_the_new_prefix(self, tmp_path):
        """A catalog too briefly offline to crawl is copied forward verbatim.
        Read it renamed, or the old spelling returns to the next export."""
        path = self.written(
            tmp_path, {"portolan:id": "a", "portolan:collection_count": 3}
        )
        link = load_links(path)["a"]
        assert link["portolan_registry:collection_count"] == 3
        assert not [k for k in link if k.startswith("portolan:")]

    def test_leaves_unprefixed_link_fields_alone(self, tmp_path):
        path = self.written(
            tmp_path, {"portolan:id": "a", "href": ROOT, "bbox": [0, 0, 1, 1]}
        )
        link = load_links(path)["a"]
        assert link["href"] == ROOT and link["bbox"] == [0, 0, 1, 1]


class TestExportSafety:
    def test_accepts_a_complete_export(self):
        e = build_export([{"id": "a", "url": ROOT}], now=FROZEN)
        check_export_safe(e, expected_ids={"a"}, previous_state={})

    def test_refuses_to_drop_a_registered_catalog(self):
        e = build_export([{"id": "a", "url": ROOT}], now=FROZEN)
        with pytest.raises(ExportRefused, match="missing 1 registered catalog"):
            check_export_safe(e, expected_ids={"a", "b"}, previous_state={})

    def test_refuses_to_discard_state_of_a_still_registered_catalog(self):
        e = build_export([{"id": "a", "url": ROOT}], now=FROZEN)
        with pytest.raises(ExportRefused, match="missing 1 registered catalog"):
            check_export_safe(
                e,
                expected_ids={"a", "b"},
                previous_state={"b": {"status": "stale", "stale_since": "2026-01-01"}},
            )

    def test_allows_deliberate_removal_of_an_entry(self):
        """Deleting a catalogs/*.yaml is intentional, so its state goes too.
        Guarding it would make removing a catalog impossible."""
        e = build_export([{"id": "a", "url": ROOT}], now=FROZEN)
        check_export_safe(
            e,
            expected_ids={"a"},
            previous_state={
                "a": {"status": "valid"},
                "deleted": {"status": "stale", "stale_since": "2026-01-01"},
            },
        )

    def test_ignores_previous_entries_with_no_state(self):
        e = build_export([{"id": "a", "url": ROOT}], now=FROZEN)
        check_export_safe(
            e,
            expected_ids={"a"},
            previous_state={
                "gone": {"status": None, "stale_since": None, "failure_reason": None}
            },
        )


class TestExportChanged:
    """What the nightly commits, and what it leaves alone."""

    LATER = FROZEN.replace(day=16)

    def written(self, tmp_path, catalogs, now=FROZEN):
        path = tmp_path / "catalogs.json"
        path.write_text(json.dumps(build_export(catalogs, now=now), indent=2))
        return path

    def catalog(self, **overrides):
        base = {
            "id": "a",
            "url": ROOT,
            "status": "valid",
            "collection_count": 3,
            "last_crawled": FROZEN.isoformat(),
            "last_validated": FROZEN.isoformat(),
        }
        return {**base, **overrides}

    def test_writes_when_there_is_no_previous_export(self, tmp_path):
        export = build_export([self.catalog()], now=FROZEN)
        assert export_changed(export, tmp_path / "catalogs.json", now=FROZEN)

    def test_ignores_a_run_that_only_moved_the_clock(self, tmp_path):
        path = self.written(tmp_path, [self.catalog()])
        tonight = self.catalog(
            last_crawled=self.LATER.isoformat(),
            last_validated=self.LATER.isoformat(),
        )
        export = build_export([tonight], now=self.LATER)
        assert not export_changed(export, path, now=self.LATER)

    def test_notices_a_status_transition(self, tmp_path):
        path = self.written(tmp_path, [self.catalog()])
        tonight = self.catalog(
            status="stale",
            stale_since=self.LATER.isoformat(),
            failure_reason="timeout",
            last_crawled=self.LATER.isoformat(),
            last_validated=self.LATER.isoformat(),
        )
        export = build_export([tonight], now=self.LATER)
        assert export_changed(export, path, now=self.LATER)

    def test_notices_a_count_change(self, tmp_path):
        path = self.written(tmp_path, [self.catalog()])
        tonight = self.catalog(collection_count=4)
        export = build_export([tonight], now=self.LATER)
        assert export_changed(export, path, now=self.LATER)

    def test_notices_a_catalog_moving_to_a_new_spec_version(self, tmp_path):
        """The whole point of tracking the version is seeing a migration."""
        path = self.written(tmp_path, [self.catalog(spec_version="0.1.0")])
        tonight = self.catalog(spec_version="0.2.0")
        export = build_export([tonight], now=self.LATER)
        assert export_changed(export, path, now=self.LATER)

    def test_notices_a_new_bbox(self, tmp_path):
        path = self.written(tmp_path, [self.catalog()])
        tonight = self.catalog(bbox=[-1.0, -2.0, 3.0, 4.0])
        export = build_export([tonight], now=self.LATER)
        assert export_changed(export, path, now=self.LATER)

    def test_notices_a_catalog_joining_the_registry(self, tmp_path):
        path = self.written(tmp_path, [self.catalog()])
        tonight = build_export(
            [self.catalog(), self.catalog(id="b")], now=self.LATER
        )
        assert export_changed(tonight, path, now=self.LATER)

    def test_notices_a_catalog_leaving_the_registry(self, tmp_path):
        path = self.written(tmp_path, [self.catalog(), self.catalog(id="b")])
        export = build_export([self.catalog()], now=self.LATER)
        assert export_changed(export, path, now=self.LATER)

    def test_overwrites_an_unreadable_export(self, tmp_path):
        path = tmp_path / "catalogs.json"
        path.write_text("{ truncated")
        assert export_changed(build_export([self.catalog()]), path, now=FROZEN)

    def test_refreshes_the_timestamps_after_a_quiet_week(self, tmp_path):
        """The timestamps are a freshness signal, so they must not freeze."""
        path = self.written(tmp_path, [self.catalog()])
        week_later = FROZEN + timedelta(days=TIMESTAMP_REFRESH_DAYS)
        export = build_export([self.catalog()], now=week_later)
        assert export_changed(export, path, now=week_later)

    def test_stays_quiet_inside_the_refresh_window(self, tmp_path):
        path = self.written(tmp_path, [self.catalog()])
        day_six = FROZEN + timedelta(days=TIMESTAMP_REFRESH_DAYS - 1)
        export = build_export([self.catalog()], now=day_six)
        assert not export_changed(export, path, now=day_six)

    def test_refreshes_an_export_with_no_readable_generated(self, tmp_path):
        path = self.written(tmp_path, [self.catalog()])
        previous = json.loads(path.read_text())
        previous["generated"] = "whenever"
        path.write_text(json.dumps(previous, indent=2))
        export = build_export([self.catalog()], now=self.LATER)
        assert export_changed(export, path, now=self.LATER)


class TestCommittedExport:
    """Guards against a bad nightly reaching the repository."""

    def test_is_readable(self):
        assert EXPORT.exists(), f"{EXPORT} not found"
        assert load_state(EXPORT)

    def test_every_child_has_an_id_and_href(self):
        for link in load_links(EXPORT).values():
            assert link.get("href")
            assert link.get("portolan_registry:id")

    def test_every_bbox_is_valid_wgs84(self):
        for cid, link in load_links(EXPORT).items():
            b = link.get("bbox")
            if b is None:
                continue
            assert len(b) in (4, 6), f"{cid}: bbox has {len(b)} elements"
            assert all(
                not math.isnan(v) and not math.isinf(v) and abs(v) < 1e300 for v in b
            ), f"{cid}: bbox has an error value"
            half = len(b) // 2
            west, south, east, north = b[0], b[1], b[half], b[half + 1]
            assert -180 <= west <= 180 and -180 <= east <= 180, f"{cid}: longitude"
            assert -90 <= south <= 90 and -90 <= north <= 90, f"{cid}: latitude"
            assert south <= north, f"{cid}: south above north"

    def test_no_portolan_prefixed_bbox(self):
        assert "portolan_registry:bbox" not in EXPORT.read_text()

    def test_carries_no_bare_portolan_prefix(self):
        """`portolan:` belongs to the specification. Registry-only fields
        answer to `portolan_registry:` so a reader can tell which is which."""
        assert '"portolan:' not in EXPORT.read_text()
