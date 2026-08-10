"""Registry entry loading and URL normalization."""

from __future__ import annotations

import pytest

from registry.entries import entry_paths, load_entries, load_entry, normalize_url


class TestNormalizeUrl:
    def test_http_becomes_https(self):
        assert normalize_url("http://ex.org/catalog.json").startswith("https://")

    def test_host_is_lowercased(self):
        assert normalize_url("https://EX.org/c.json") == "https://ex.org/c.json"

    def test_www_is_dropped(self):
        assert normalize_url("https://www.ex.org/c.json") == "https://ex.org/c.json"

    def test_trailing_slash_is_dropped(self):
        assert normalize_url("https://ex.org/a/") == "https://ex.org/a"

    def test_query_and_fragment_are_dropped(self):
        assert normalize_url("https://ex.org/c.json?v=1#x") == "https://ex.org/c.json"

    def test_variants_of_the_same_catalog_collide(self):
        a = normalize_url("http://WWW.Ex.org/data/catalog.json")
        b = normalize_url("https://ex.org/data/catalog.json")
        assert a == b

    def test_different_paths_do_not_collide(self):
        a = normalize_url("https://ex.org/a/catalog.json")
        b = normalize_url("https://ex.org/b/catalog.json")
        assert a != b


class TestLoadEntries:
    def test_reads_url_from_a_file(self, tmp_path):
        (tmp_path / "one.yaml").write_text("url: https://ex.org/catalog.json\n")
        assert load_entry(tmp_path / "one.yaml") == {
            "url": "https://ex.org/catalog.json"
        }

    def test_empty_file_is_an_empty_dict(self, tmp_path):
        (tmp_path / "empty.yaml").write_text("")
        assert load_entry(tmp_path / "empty.yaml") == {}

    def test_entries_are_keyed_by_filename_stem(self, tmp_path):
        (tmp_path / "alpha.yaml").write_text("url: https://ex.org/a/catalog.json\n")
        (tmp_path / "beta.yaml").write_text("url: https://ex.org/b/catalog.json\n")
        assert set(load_entries(tmp_path)) == {"alpha", "beta"}

    def test_paths_are_sorted(self, tmp_path):
        for name in ("zeta", "alpha", "mu"):
            (tmp_path / f"{name}.yaml").write_text("url: https://ex.org/catalog.json\n")
        assert [p.stem for p in entry_paths(tmp_path)] == ["alpha", "mu", "zeta"]

    def test_missing_directory_yields_nothing(self, tmp_path):
        assert entry_paths(tmp_path / "nope") == []


class TestRealCatalogDir:
    def test_every_registered_entry_has_a_catalog_json_url(self):
        entries = load_entries()
        assert entries, "no catalog entries found"
        for cid, entry in entries.items():
            assert entry.get("url"), f"{cid}: missing url"
            assert entry["url"].endswith("catalog.json"), f"{cid}: bad url"

    def test_no_duplicate_catalog_urls(self):
        seen: dict[str, str] = {}
        for cid, entry in load_entries().items():
            norm = normalize_url(entry["url"])
            assert norm not in seen, f"{cid} duplicates {seen.get(norm)}"
            seen[norm] = cid
