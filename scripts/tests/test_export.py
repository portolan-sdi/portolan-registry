"""Export assembly, the golden comparison, and the no-data-loss guard."""

from __future__ import annotations

import json
import math

import pytest

from conftest import FIXTURES, FROZEN
from registry.crawl import crawl_catalog
from registry.export import (
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
        assert "portolan:bbox" not in link

    def test_omits_bbox_when_unknown(self):
        assert "bbox" not in child_link({"id": "x", "url": ROOT})

    def test_omits_bbox_when_none(self):
        assert "bbox" not in child_link({"id": "x", "url": ROOT, "bbox": None})

    def test_falls_back_to_id_for_title(self):
        assert child_link({"id": "x", "url": ROOT})["title"] == "x"

    def test_registry_metadata_is_prefixed(self):
        link = child_link({"id": "x", "url": ROOT, "collection_count": 7})
        assert link["portolan:id"] == "x"
        assert link["portolan:collection_count"] == 7


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
        ids = [link["portolan:id"] for link in e["links"] if link["rel"] == "child"]
        assert ids == ["a", "z"]

    def test_carried_links_are_counted_and_sorted_in(self):
        e = build_export(
            [{"id": "b", "url": ROOT}],
            now=FROZEN,
            extra_links=[{"rel": "child", "portolan:id": "a", "href": ROOT}],
        )
        ids = [link["portolan:id"] for link in e["links"] if link["rel"] == "child"]
        assert ids == ["a", "b"]
        assert e["count"] == 2

    def test_is_a_stac_catalog(self):
        e = build_export([], now=FROZEN)
        assert e["type"] == "Catalog"
        assert e["stac_version"] == "1.1.0"
        assert e["id"] == "portolan-registry"


class TestGolden:
    def test_extraction_preserves_pre_refactor_output(self, tree):
        """The golden was produced by the pre-extraction crawler against these
        same fixtures, so a match is evidence the move changed nothing."""
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
        assert export_changed(export, tmp_path / "catalogs.json")

    def test_ignores_a_run_that_only_moved_the_clock(self, tmp_path):
        path = self.written(tmp_path, [self.catalog()])
        tonight = self.catalog(
            last_crawled=self.LATER.isoformat(),
            last_validated=self.LATER.isoformat(),
        )
        assert not export_changed(build_export([tonight], now=self.LATER), path)

    def test_notices_a_status_transition(self, tmp_path):
        path = self.written(tmp_path, [self.catalog()])
        tonight = self.catalog(
            status="stale",
            stale_since=self.LATER.isoformat(),
            failure_reason="timeout",
            last_crawled=self.LATER.isoformat(),
            last_validated=self.LATER.isoformat(),
        )
        assert export_changed(build_export([tonight], now=self.LATER), path)

    def test_notices_a_count_change(self, tmp_path):
        path = self.written(tmp_path, [self.catalog()])
        tonight = self.catalog(
            collection_count=4, last_crawled=self.LATER.isoformat()
        )
        assert export_changed(build_export([tonight], now=self.LATER), path)

    def test_notices_a_new_bbox(self, tmp_path):
        path = self.written(tmp_path, [self.catalog()])
        tonight = self.catalog(bbox=[-1.0, -2.0, 3.0, 4.0])
        assert export_changed(build_export([tonight], now=self.LATER), path)

    def test_notices_a_catalog_joining_the_registry(self, tmp_path):
        path = self.written(tmp_path, [self.catalog()])
        tonight = build_export(
            [self.catalog(), self.catalog(id="b")], now=self.LATER
        )
        assert export_changed(tonight, path)

    def test_notices_a_catalog_leaving_the_registry(self, tmp_path):
        path = self.written(tmp_path, [self.catalog(), self.catalog(id="b")])
        assert export_changed(build_export([self.catalog()], now=self.LATER), path)

    def test_overwrites_an_unreadable_export(self, tmp_path):
        path = tmp_path / "catalogs.json"
        path.write_text("{ truncated")
        assert export_changed(build_export([self.catalog()], now=FROZEN), path)


class TestCommittedExport:
    """Guards against a bad nightly reaching the repository."""

    def test_is_readable(self):
        assert EXPORT.exists(), f"{EXPORT} not found"
        assert load_state(EXPORT)

    def test_every_child_has_an_id_and_href(self):
        for link in load_links(EXPORT).values():
            assert link.get("href")
            assert link.get("portolan:id")

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
        assert "portolan:bbox" not in EXPORT.read_text()
