"""The pull request gate: entry checks and the report it leaves behind.

Address validation resolves MX records, so these tests replace the library
call with a double rather than reaching the network. The validators run as
subprocesses, so they are replaced too, with a double that reports whatever
a test asks. What is under test is the gate's own behavior: which checks run,
in what order, which entries they gate, and what the notifier can read
afterwards.
"""

from __future__ import annotations

import json

import pytest
import validate_entries
from conftest import FakeFetcher
from registry import contacts, validators
from registry.validators import Finding, ValidatorError
from test_validators import TOOLS

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


REAL_VALIDATE = validators.validate_mirror


class Reported(list):
    """Findings the validator double returns. Set `error` to make it fail."""

    error: str | None = None


@pytest.fixture(autouse=True)
def findings(monkeypatch):
    reported = Reported()

    def validate(mirror_dir):
        if reported.error:
            raise ValidatorError(reported.error)
        return list(reported)

    monkeypatch.setattr(validators, "validate_mirror", validate)
    return reported


ENTRY = f"url: {ROOT}\nsubmitter_email: submitter@example.com\n"


def published(catalog_id="cat", href=ROOT, status="valid"):
    return {
        catalog_id: {
            "href": href,
            "portolan_registry:id": catalog_id,
            "portolan_registry:status": status,
        }
    }


def entry_file(tmp_path, name, body):
    path = tmp_path / name
    path.write_text(body)
    return path


def check(path, fetcher, **kw):
    return validate_entries.check_entry(
        path,
        existing_urls=kw.get("existing_urls", {}),
        state=kw.get("state", {}),
        source_factory=lambda: fetcher,
        published=kw.get("published"),
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
            tmp_path,
            "cat.yaml",
            f"url: {ROOT}\nsubmitter_email: submitter@example.com\n",
        )
        assert check(path, tree) == []
        assert ROOT in tree.calls


class TestValidatorGate:
    def test_a_new_entry_with_a_rashid_error_fails_and_names_the_rule(
        self, tmp_path, tree, findings
    ):
        findings.append(
            Finding("rashid", "PTL-CNF-001", "declare the schema", "catalog.json", "none")
        )
        errors = check(entry_file(tmp_path, "cat.yaml", ENTRY), tree)
        assert len(errors) == 1
        assert "rashid PTL-CNF-001 (1 finding): declare the schema" in errors[0]
        assert "`catalog.json: none`" in errors[0]

    def test_one_error_per_rule(self, tmp_path, tree, findings):
        findings.extend(
            [Finding("rashid", "A", "a", f"{i}.json", "m") for i in range(5)]
            + [Finding("stac-node-validator", "core", "c", "x.json", "m")]
        )
        errors = check(entry_file(tmp_path, "cat.yaml", ENTRY), tree)
        assert len(errors) == 2
        assert "and 2 more" in errors[0]

    def test_a_clean_new_entry_passes(self, tmp_path, tree):
        assert check(entry_file(tmp_path, "cat.yaml", ENTRY), tree) == []

    def test_a_new_entry_with_a_broken_child_fails(self, tmp_path, tree):
        tree.docs["https://ex.org/sub/catalog.json"] = TimeoutError("read timed out")
        errors = check(entry_file(tmp_path, "cat.yaml", ENTRY), tree)
        assert len(errors) == 1
        assert "fetch FETCH" in errors[0]
        assert "https://ex.org/sub/catalog.json" in errors[0]

    def test_a_validator_that_cannot_run_fails_a_new_entry(self, tmp_path, tree, findings):
        findings.error = "rashid is not installed"
        errors = check(entry_file(tmp_path, "cat.yaml", ENTRY), tree)
        assert errors == [
            f"{tmp_path / 'cat.yaml'}: The validators could not run: rashid is not installed"
        ]

    def test_a_registered_entry_reports_and_does_not_fail(self, tmp_path, tree, findings):
        """8 of 13 registered catalogs failed rashid in August 2026."""
        findings.append(Finding("rashid", "PTL-CNF-001", "d", "catalog.json", "m"))
        path = entry_file(tmp_path, "cat.yaml", ENTRY)
        assert check(path, tree, published=published()) == []

    def test_a_registered_entry_at_a_new_url_is_gated(self, tmp_path, tree, findings):
        findings.append(Finding("rashid", "PTL-CNF-001", "d", "catalog.json", "m"))
        path = entry_file(tmp_path, "cat.yaml", ENTRY)
        links = published(href="https://elsewhere.org/catalog.json")
        assert len(check(path, tree, published=links)) == 1

    def test_a_removed_entry_is_gated_again(self, tmp_path, tree, findings):
        findings.append(Finding("rashid", "PTL-CNF-001", "d", "catalog.json", "m"))
        path = entry_file(tmp_path, "cat.yaml", ENTRY)
        assert len(check(path, tree, published=published(status="removed"))) == 1

    def test_another_catalog_being_registered_does_not_exempt_this_one(
        self, tmp_path, tree, findings
    ):
        findings.append(Finding("rashid", "PTL-CNF-001", "d", "catalog.json", "m"))
        path = entry_file(tmp_path, "cat.yaml", ENTRY)
        assert len(check(path, tree, published=published(catalog_id="dog"))) == 1


class EmptyFetcher(FakeFetcher):
    """Answers `{}` for every URL. The old gate passed this."""

    def get_json(self, url, timeout=30):
        self.calls.append(url)
        return {}

    def get_bytes(self, url, timeout=30):
        self.calls.append(f"BYTES {url}")
        return b"{}"


@pytest.mark.skipif(not TOOLS, reason="rashid and stac-node-validator are not installed")
def test_a_root_that_returns_empty_json_exits_nonzero(tmp_path, monkeypatch):
    """Issue #200, done-when 3, with the real rashid and stac-node-validator."""
    monkeypatch.setattr(validators, "validate_mirror", REAL_VALIDATE)
    monkeypatch.setattr(validate_entries, "HttpFetcher", EmptyFetcher)
    entry_file(tmp_path, "cat.yaml", "url: https://x.invalid/catalog.json\n"
               "submitter_email: submitter@example.com\n")
    changed = tmp_path / "changed.txt"
    changed.write_text(f"{tmp_path / 'cat.yaml'}\n")
    report = tmp_path / "report.json"
    code = validate_entries.main(
        ["--changed-file", str(changed), "--catalog-dir", str(tmp_path), "--report", str(report)]
    )
    assert code == 1
    errors = json.loads(report.read_text())["errors"]
    assert any(" rashid PTL-" in e for e in errors)


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


class TestDeletedEntry:
    """A deletion must fail the gate, not leave it nothing to check."""

    def run(self, tmp_path, listed):
        changed = tmp_path / "changed.txt"
        changed.write_text("".join(f"{p}\n" for p in listed))
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
        return code, json.loads(report.read_text())

    def test_a_deleted_entry_fails_the_gate(self, tmp_path):
        code, report = self.run(tmp_path, [tmp_path / "cadastral.yaml"])
        assert code == 1
        assert report["ok"] is False
        assert len(report["errors"]) == 1
        assert "cadastral.yaml" in report["errors"][0]
        assert "Deleting an entry is not allowed" in report["errors"][0]

    def test_the_message_names_the_removal_path(self, tmp_path):
        _, report = self.run(tmp_path, [tmp_path / "cadastral.yaml"])
        assert "status: removed" in report["errors"][0]

    def test_a_deletion_fails_beside_a_valid_entry(self, tmp_path, monkeypatch):
        """A rename lists the old path as deleted and the new path as added."""
        monkeypatch.setattr(validate_entries, "check_entry", lambda *a, **k: [])
        entry_file(tmp_path, "new.yaml", f"url: {ROOT}\n")
        code, report = self.run(
            tmp_path, [tmp_path / "old.yaml", tmp_path / "new.yaml"]
        )
        assert code == 1
        assert len(report["errors"]) == 1
        assert "old.yaml" in report["errors"][0]

    def test_deleted_entries_ignores_existing_paths(self, tmp_path):
        present = entry_file(tmp_path, "cat.yaml", f"url: {ROOT}\n")
        changed = tmp_path / "changed.txt"
        changed.write_text(f"{present}\n\n")
        assert validate_entries.deleted_entries(changed) == []


class TestCurrentEntryChange:
    """Only a new entry passes without a maintainer approval."""

    @pytest.fixture(autouse=True)
    def no_crawl(self, monkeypatch):
        monkeypatch.setattr(validate_entries, "check_entry", lambda *a, **k: [])

    def run(self, tmp_path, changed, added, approved=False):
        changed_file = tmp_path / "changed.txt"
        changed_file.write_text("".join(f"{p}\n" for p in changed))
        added_file = tmp_path / "added.txt"
        added_file.write_text("".join(f"{p}\n" for p in added))
        return validate_entries.collect_errors(
            changed_file=changed_file,
            catalog_dir=tmp_path,
            added_file=added_file,
            maintainer_approved=approved,
        )

    def test_a_new_entry_passes(self, tmp_path):
        new = entry_file(tmp_path, "new.yaml", f"url: {ROOT}\n")
        assert self.run(tmp_path, [new], [new]) == []

    def test_an_edited_entry_fails(self, tmp_path):
        current = entry_file(tmp_path, "cadastral.yaml", f"url: {ROOT}\n")
        errors = self.run(tmp_path, [current], [])
        assert len(errors) == 1
        assert "cadastral.yaml" in errors[0]
        assert "changes a current entry" in errors[0]
        assert "maintainer" in errors[0]

    def test_an_edit_fails_beside_a_new_entry(self, tmp_path):
        new = entry_file(tmp_path, "new.yaml", f"url: {ROOT}\n")
        current = entry_file(tmp_path, "cadastral.yaml", f"url: {ROOT}\n")
        errors = self.run(tmp_path, [new, current], [new])
        assert len(errors) == 1
        assert "cadastral.yaml" in errors[0]

    def test_a_relative_listing_matches_an_absolute_one(self, tmp_path, monkeypatch):
        """git prints relative paths. The match must not depend on the form."""
        monkeypatch.chdir(tmp_path)
        entry_file(tmp_path, "new.yaml", f"url: {ROOT}\n")
        assert self.run(tmp_path, ["new.yaml"], [tmp_path / "new.yaml"]) == []

    def test_an_approval_lets_an_edit_pass(self, tmp_path):
        current = entry_file(tmp_path, "cadastral.yaml", f"url: {ROOT}\n")
        assert self.run(tmp_path, [current], [], approved=True) == []

    def test_an_approval_lets_a_deletion_pass(self, tmp_path):
        gone = tmp_path / "cadastral.yaml"
        assert self.run(tmp_path, [gone], [], approved=True) == []

    def test_an_approved_edit_is_still_validated(self, tmp_path, monkeypatch):
        """The approval waives the review rule, not the entry checks."""
        current = entry_file(tmp_path, "cadastral.yaml", f"url: {ROOT}\n")
        monkeypatch.setattr(
            validate_entries, "check_entry", lambda path, **k: [f"{path}: bad"]
        )
        errors = self.run(tmp_path, [current], [], approved=True)
        assert errors == [f"{current}: bad"]

    def test_main_passes_the_new_flags_through(self, tmp_path):
        current = entry_file(tmp_path, "cadastral.yaml", f"url: {ROOT}\n")
        changed = tmp_path / "changed.txt"
        changed.write_text(f"{current}\n")
        added = tmp_path / "added.txt"
        added.write_text("")
        args = [
            "--changed-file",
            str(changed),
            "--added-file",
            str(added),
            "--catalog-dir",
            str(tmp_path),
        ]
        assert validate_entries.main(args) == 1
        assert validate_entries.main([*args, "--maintainer-approved"]) == 0


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
