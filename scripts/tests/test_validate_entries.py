"""The pull request gate: entry checks and the report it leaves behind.

Address validation resolves MX records, so these tests replace the library
call with a double rather than reaching the network. What is under test is
the gate's own behavior: which checks run, in what order, and what the
notifier can read afterwards.
"""

from __future__ import annotations

import json

import pytest
import validate_entries
from conftest import FakeFetcher
from registry import contacts

ROOT = "https://ex.org/catalog.json"


@pytest.fixture(autouse=True)
def no_dns(monkeypatch):
    """Keep the library's syntax check. Drop its MX lookup."""
    real = contacts.validate_email
    monkeypatch.setattr(
        contacts,
        "validate_email",
        lambda email, check_deliverability=True: real(
            email, check_deliverability=False
        ),
    )


def entry_file(tmp_path, name, body):
    path = tmp_path / name
    path.write_text(body)
    return path


def check(path, fetcher, **kw):
    return validate_entries.check_entry(
        path,
        existing_urls=kw.get("existing_urls", {}),
        state=kw.get("state", {}),
        fetcher=fetcher,
    )


class TestSubmitterAddress:
    def test_a_missing_address_is_an_error(self, tmp_path):
        path = entry_file(tmp_path, "cat.yaml", f"url: {ROOT}\n")
        fetcher = FakeFetcher()
        errors = check(path, fetcher)
        assert len(errors) == 1
        assert "Missing submitter_email" in errors[0]

    def test_a_missing_address_stops_the_crawl(self, tmp_path):
        """One entry, no HTTP: the address is checked before the tree walk."""
        path = entry_file(tmp_path, "cat.yaml", f"url: {ROOT}\n")
        fetcher = FakeFetcher()
        check(path, fetcher)
        assert fetcher.calls == []

    def test_a_malformed_address_is_an_error(self, tmp_path):
        path = entry_file(
            tmp_path, "cat.yaml", f"url: {ROOT}\nsubmitter_email: not-an-email\n"
        )
        errors = check(path, FakeFetcher())
        assert len(errors) == 1
        assert "Invalid submitter_email" in errors[0]

    def test_the_catalog_id_names_the_offending_file(self, tmp_path):
        path = entry_file(tmp_path, "jrc-glofas.yaml", f"url: {ROOT}\n")
        errors = check(path, FakeFetcher())
        assert "jrc-glofas" in errors[0]

    def test_a_valid_address_reaches_the_crawl(self, tmp_path, tree):
        path = entry_file(
            tmp_path, "cat.yaml", f"url: {ROOT}\nsubmitter_email: submitter@example.com\n"
        )
        assert check(path, tree) == []
        assert ROOT in tree.calls


class TestReport:
    def test_a_clean_run_records_no_errors(self, tmp_path):
        path = tmp_path / "report.json"
        validate_entries.write_report(path, [])
        assert json.loads(path.read_text()) == {"ok": True, "errors": []}

    def test_errors_are_recorded_verbatim(self, tmp_path):
        path = tmp_path / "report.json"
        validate_entries.write_report(path, ["cat.yaml: bad", "dog.yaml: worse"])
        report = json.loads(path.read_text())
        assert report["ok"] is False
        assert report["errors"] == ["cat.yaml: bad", "dog.yaml: worse"]

    def test_main_writes_the_report_it_is_asked_for(self, tmp_path):
        changed = tmp_path / "changed.txt"
        changed.write_text("")
        report = tmp_path / "report.json"

        code = validate_entries.main(
            [
                "--changed-file",
                str(changed),
                "--catalog-dir",
                str(tmp_path),
                "--report",
                str(report),
            ]
        )
        assert code == 0
        assert json.loads(report.read_text()) == {"ok": True, "errors": []}

    def test_main_reports_a_failing_entry_and_exits_nonzero(self, tmp_path):
        entry_file(tmp_path, "cat.yaml", f"url: {ROOT}\n")
        changed = tmp_path / "changed.txt"
        changed.write_text(f"{tmp_path / 'cat.yaml'}\n")
        report = tmp_path / "report.json"

        code = validate_entries.main(
            [
                "--changed-file",
                str(changed),
                "--catalog-dir",
                str(tmp_path),
                "--report",
                str(report),
            ]
        )
        assert code == 1
        assert json.loads(report.read_text())["ok"] is False


class TestUnexpectedFailure:
    def test_a_crash_becomes_an_error_rather_than_a_traceback(
        self, tmp_path, monkeypatch
    ):
        """The submitter gets a reason, not a red check and silence."""

        def boom(_path):
            raise OSError("export unreadable")

        monkeypatch.setattr(validate_entries, "load_state", boom)
        errors = validate_entries.collect_errors(
            changed_file=tmp_path / "changed.txt", catalog_dir=tmp_path
        )
        assert len(errors) == 1
        assert "export unreadable" in errors[0]
