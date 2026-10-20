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
import subprocess

import pytest
import validate_entries
from conftest import FakeFetcher
from test_validators import TOOLS

from registry import contacts, validators
from registry.validators import Finding, ValidatorError

ROOT = "https://ex.org/catalog.json"


@pytest.fixture(autouse=True)
def no_dns(monkeypatch):
    """Keep the library's syntax check. Drop its MX lookup."""
    real = contacts.validate_email
    monkeypatch.setattr(
        contacts,
        "validate_email",
        lambda email, check_deliverability=True: real(email, check_deliverability=False),
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
        path = entry_file(tmp_path, "cat.yaml", f"url: {ROOT}\nsubmitter_email: not-an-email\n")
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
    entry_file(
        tmp_path,
        "cat.yaml",
        "url: https://x.invalid/catalog.json\nsubmitter_email: submitter@example.com\n",
    )
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
        assert "This pull request deletes an entry" in report["errors"][0]

    def test_the_message_names_the_removal_path(self, tmp_path):
        """Issue #234: the old message said to keep the file, which was wrong."""
        _, report = self.run(tmp_path, [tmp_path / "cadastral.yaml"])
        message = report["errors"][0]
        assert validate_entries.REMOVAL_FORM in message
        assert validate_entries.APPROVAL_HINT in message
        assert "Keep the file" not in message

    def test_a_deletion_fails_beside_a_valid_entry(self, tmp_path, monkeypatch):
        """A rename lists the old path as deleted and the new path as added."""
        monkeypatch.setattr(validate_entries, "check_entry", lambda *a, **k: [])
        entry_file(tmp_path, "new.yaml", f"url: {ROOT}\n")
        code, report = self.run(tmp_path, [tmp_path / "old.yaml", tmp_path / "new.yaml"])
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
        monkeypatch.setattr(validate_entries, "check_entry", lambda path, **k: [f"{path}: bad"])
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


class TestPublishedExport:
    """The export decides which entries are new. CI reads the base copy."""

    def run(self, tmp_path, export, findings):
        findings.append(Finding("rashid", "PTL-CNF-001", "d", "catalog.json", "m"))
        new = entry_file(tmp_path, "evil.yaml", ENTRY)
        changed = tmp_path / "changed.txt"
        changed.write_text(f"{new}\n")
        return validate_entries.collect_errors(
            changed_file=changed, catalog_dir=tmp_path, added_file=changed, export_path=export
        )

    def export(self, path, links):
        path.write_text(json.dumps({"type": "Catalog", "links": links}))
        return path

    def test_an_entry_listed_only_in_the_pull_request_export_is_gated(
        self, tmp_path, tree, findings, monkeypatch
    ):
        """A fork can add its own entry to exports/catalogs.json. The gate
        reads the base copy, which does not hold it, so it stays new."""
        monkeypatch.setattr(validate_entries, "HttpFetcher", lambda: tree)
        listed = {
            "rel": "child",
            "href": ROOT,
            "portolan_registry:id": "evil",
            "portolan_registry:status": "valid",
        }
        # The pull request's own copy, where the default path points.
        monkeypatch.chdir(tmp_path)
        (tmp_path / "exports").mkdir()
        self.export(tmp_path / "exports" / "catalogs.json", [listed])
        base = self.export(tmp_path / "base-export.json", [])
        errors = self.run(tmp_path, base, findings)
        assert len(errors) == 1
        assert "PTL-CNF-001" in errors[0]

    def test_an_entry_in_the_base_export_is_not_gated(self, tmp_path, tree, findings, monkeypatch):
        monkeypatch.setattr(validate_entries, "HttpFetcher", lambda: tree)
        listed = {
            "rel": "child",
            "href": ROOT,
            "portolan_registry:id": "evil",
            "portolan_registry:status": "valid",
        }
        base = self.export(tmp_path / "base-export.json", [listed])
        assert self.run(tmp_path, base, findings) == []

    def test_main_reads_the_export_it_is_given(self, tmp_path, monkeypatch):
        seen = []
        monkeypatch.setattr(validate_entries, "load_links", lambda p: seen.append(p) or {})
        monkeypatch.setattr(validate_entries, "load_state", lambda p: {})
        changed = tmp_path / "changed.txt"
        changed.write_text("")
        validate_entries.main(
            [
                "--changed-file",
                str(changed),
                "--catalog-dir",
                str(tmp_path),
                "--export",
                str(tmp_path / "base-export.json"),
            ]
        )
        assert seen == [tmp_path / "base-export.json"]


class TestUnexpectedFailure:
    def test_a_crash_becomes_an_error_rather_than_a_traceback(self, tmp_path, monkeypatch):
        """The submitter gets a reason, not a red check and silence."""

        def boom(_path):
            raise OSError("export unreadable")

        monkeypatch.setattr(validate_entries, "load_state", boom)
        errors = validate_entries.collect_errors(
            changed_file=tmp_path / "changed.txt", catalog_dir=tmp_path
        )
        assert len(errors) == 1
        assert "export unreadable" in errors[0]


def git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


class TestDuplicateUrl:
    """The duplicate check must not depend on which filename sorts last.

    roborev found that a map with one file per URL kept the file that
    sorted last. A new entry whose name sorted after the current holder
    found itself in the map and passed.
    """

    def run(self, catalog_dir, new, base_ref=None):
        changed = catalog_dir.parent / "changed.txt"
        changed.write_text(f"{new}\n")
        return validate_entries.collect_errors(
            changed_file=changed,
            catalog_dir=catalog_dir,
            added_file=changed,
            base_ref=base_ref,
        )

    @pytest.fixture
    def catalogs(self, tmp_path):
        catalogs = tmp_path / "catalogs"
        catalogs.mkdir()
        entry_file(catalogs, "new-id.yaml", ENTRY)
        return catalogs

    @pytest.mark.parametrize("stem", ["alpha", "zeta"])
    def test_a_duplicate_fails_whichever_name_sorts_last(self, catalogs, stem):
        new = entry_file(catalogs, f"{stem}.yaml", ENTRY)
        errors = self.run(catalogs, new)
        assert len(errors) == 1
        assert "Duplicate catalog URL detected" in errors[0]
        assert "'new-id.yaml'" in errors[0]

    def test_a_duplicate_of_a_url_a_removed_id_held_fails_once(self, catalogs):
        """The URL rule stays quiet and the duplicate check reports it."""
        git("init", "-q", "-b", "main", cwd=catalogs)
        git("config", "user.email", "test@example.org", cwd=catalogs)
        git("config", "user.name", "Test", cwd=catalogs)
        git("config", "commit.gpgsign", "false", cwd=catalogs)
        (catalogs / "new-id.yaml").unlink()
        entry_file(catalogs, "old.yaml", ENTRY)
        git("add", "-A", cwd=catalogs)
        git("commit", "-q", "-m", "add old", cwd=catalogs)
        git("rm", "-q", "old.yaml", cwd=catalogs)
        git("commit", "-q", "-m", "remove old", cwd=catalogs)
        entry_file(catalogs, "new-id.yaml", ENTRY)
        git("add", "-A", cwd=catalogs)
        git("commit", "-q", "-m", "add new-id", cwd=catalogs)
        new = entry_file(catalogs, "zeta.yaml", ENTRY)
        errors = self.run(catalogs, new, base_ref="main")
        assert len(errors) == 1
        assert "Duplicate catalog URL detected" in errors[0]


class TestRegistryIds:
    """Issue #242: an ID never changes and never names a second catalog.

    Each test builds a throwaway repository with a `main` branch and a pull
    request branch. The history rules read `main`, as CI reads the base
    branch.
    """

    @pytest.fixture(autouse=True)
    def no_crawl(self, monkeypatch):
        monkeypatch.setattr(validate_entries, "check_entry", lambda *a, **k: [])

    @pytest.fixture
    def repo(self, tmp_path):
        git("init", "-q", "-b", "main", cwd=tmp_path)
        git("config", "user.email", "test@example.org", cwd=tmp_path)
        git("config", "user.name", "Test", cwd=tmp_path)
        git("config", "commit.gpgsign", "false", cwd=tmp_path)
        catalogs = tmp_path / "catalogs"
        catalogs.mkdir()
        self.write(catalogs, "seed", url="https://seed.org/catalog.json")
        self.commit(catalogs, "seed")
        return catalogs

    def write(self, catalogs, stem, url=ROOT):
        path = catalogs / f"{stem}.yaml"
        path.write_text(f"url: {url}\nsubmitter_email: submitter@example.com\n")
        return path

    def commit(self, catalogs, message):
        git("add", "-A", cwd=catalogs)
        git("commit", "-q", "-m", message, cwd=catalogs)

    def registered_then_removed(self, catalogs, stem, url=ROOT):
        """On main: register an entry, then remove it."""
        self.write(catalogs, stem, url=url)
        self.commit(catalogs, f"add {stem}")
        (catalogs / f"{stem}.yaml").unlink()
        self.commit(catalogs, f"remove {stem}")

    def open_pull_request(self, catalogs):
        git("switch", "-q", "-c", "pr", cwd=catalogs)

    def run(self, catalogs, changed, added, approved=False, base_ref="main"):
        changed_file = catalogs.parent / "changed.txt"
        changed_file.write_text("".join(f"{p}\n" for p in changed))
        added_file = catalogs.parent / "added.txt"
        added_file.write_text("".join(f"{p}\n" for p in added))
        return validate_entries.collect_errors(
            changed_file=changed_file,
            catalog_dir=catalogs,
            added_file=added_file,
            maintainer_approved=approved,
            base_ref=base_ref,
        )

    def test_a_new_id_passes(self, repo):
        self.open_pull_request(repo)
        new = self.write(repo, "city-of-example-parcels")
        assert self.run(repo, [new], [new]) == []

    @pytest.mark.parametrize(
        "stem", ["Parcels", "city_parcels", "city--parcels", "-parcels", "a.b"]
    )
    def test_an_id_outside_the_pattern_fails(self, repo, stem):
        new = self.write(repo, stem)
        errors = self.run(repo, [new], [new], approved=True)
        assert len(errors) == 1
        assert "not a valid registry ID" in errors[0]

    def test_a_current_id_outside_the_pattern_is_not_checked(self, repo):
        """The rule gates new IDs. An edit keeps the ID it has."""
        current = self.write(repo, "Legacy_Name")
        assert self.run(repo, [current], [], approved=True) == []

    def test_a_rename_fails_even_with_an_approval(self, repo):
        self.write(repo, "old-name")
        self.commit(repo, "add old-name")
        self.open_pull_request(repo)
        git("mv", "old-name.yaml", "new-name.yaml", cwd=repo)
        self.commit(repo, "rename")
        old, new = repo / "old-name.yaml", repo / "new-name.yaml"
        errors = self.run(repo, [old, new], [new], approved=True)
        assert len(errors) == 1
        assert "renames 'old-name' to 'new-name'" in errors[0]
        assert "Keep old-name.yaml" in errors[0]

    def test_an_uncommitted_rename_still_fails(self, repo):
        """The old URL comes from the base branch, not from the PR commits."""
        self.write(repo, "old-name")
        self.commit(repo, "add old-name")
        self.open_pull_request(repo)
        (repo / "old-name.yaml").unlink()
        new = self.write(repo, "new-name")
        errors = self.run(repo, [repo / "old-name.yaml", new], [new], approved=True)
        assert len(errors) == 1
        assert "renames" in errors[0]

    def test_a_rename_that_respells_the_url_still_fails(self, repo):
        self.write(repo, "old-name", url="https://www.ex.org/catalog.json")
        self.commit(repo, "add old-name")
        self.open_pull_request(repo)
        (repo / "old-name.yaml").unlink()
        new = self.write(repo, "new-name", url="https://ex.org/catalog.json")
        self.commit(repo, "rename")
        errors = self.run(repo, [repo / "old-name.yaml", new], [new], approved=True)
        assert len(errors) == 1
        assert "renames" in errors[0]

    def test_a_rename_split_across_two_pull_requests_fails(self, repo):
        """Review finding: the first PR removes the entry, the second re-adds
        its URL under a new name."""
        self.registered_then_removed(repo, "original")
        self.open_pull_request(repo)
        renamed = self.write(repo, "renamed")
        errors = self.run(repo, [renamed], [renamed], approved=True)
        assert len(errors) == 1
        assert "belonged to registry ID 'original'" in errors[0]
        assert "name the file original.yaml" in errors[0]

    def test_a_url_that_moved_to_a_current_entry_is_not_an_id_error(self, repo):
        """The duplicate URL check owns this case. The ID rule stays quiet."""
        self.registered_then_removed(repo, "original")
        self.write(repo, "original")
        self.commit(repo, "bring original back")
        self.open_pull_request(repo)
        other = self.write(repo, "other")
        assert self.run(repo, [other], [other], approved=True) == []

    def test_a_url_that_moved_to_another_current_id_is_not_an_id_error(self, repo):
        """Review finding: the old hint would name an ID that fails too."""
        self.registered_then_removed(repo, "old")
        self.write(repo, "new-id")
        self.commit(repo, "register the catalog as new-id")
        self.open_pull_request(repo)
        bar = self.write(repo, "bar")
        assert self.run(repo, [bar], [bar], approved=True) == []

    def test_an_id_whose_newest_deletion_held_another_url_is_not_named(self, repo):
        """Review finding: the history renamed `main.yaml` twice, once per
        catalog. The hint must not name an ID that now points elsewhere."""
        self.registered_then_removed(repo, "main", url="https://madrid.org/catalog.json")
        self.registered_then_removed(repo, "main", url="https://finland.org/catalog.json")
        self.open_pull_request(repo)
        madrid = self.write(repo, "madrid", url="https://madrid.org/catalog.json")
        assert self.run(repo, [madrid], [madrid], approved=True) == []
        finland = self.write(repo, "finland", url="https://finland.org/catalog.json")
        errors = self.run(repo, [finland], [finland], approved=True)
        assert len(errors) == 1
        assert "name the file main.yaml" in errors[0]

    def test_a_deletion_beside_an_unrelated_addition_passes_with_an_approval(self, repo):
        self.write(repo, "leaving")
        self.commit(repo, "add leaving")
        self.open_pull_request(repo)
        (repo / "leaving.yaml").unlink()
        new = self.write(repo, "arriving", url="https://other.org/catalog.json")
        self.commit(repo, "swap")
        assert self.run(repo, [repo / "leaving.yaml", new], [new], approved=True) == []

    def test_a_retired_id_fails_for_another_catalog(self, repo):
        self.registered_then_removed(repo, "placeholder")
        self.open_pull_request(repo)
        new = self.write(repo, "placeholder", url="https://other.org/catalog.json")
        errors = self.run(repo, [new], [new], approved=True)
        assert len(errors) == 1
        assert "belonged to another catalog" in errors[0]
        assert "Choose a different filename" in errors[0]

    def test_the_same_catalog_needs_an_approval_to_return(self, repo):
        self.registered_then_removed(repo, "placeholder")
        self.open_pull_request(repo)
        back = self.write(repo, "placeholder")
        errors = self.run(repo, [back], [back])
        assert len(errors) == 1
        assert "register the same catalog again" in errors[0]
        assert validate_entries.APPROVAL_HINT in errors[0]

    def test_an_approval_lets_the_same_catalog_return(self, repo):
        self.registered_then_removed(repo, "placeholder")
        self.open_pull_request(repo)
        back = self.write(repo, "placeholder", url="https://www.ex.org/catalog.json/")
        assert self.run(repo, [back], [back], approved=True) == []

    def test_a_merge_cannot_hide_a_retired_id(self, repo):
        """Review finding: a branch keeps an ID that main deleted, points it
        at another catalog, and merges main. The merge keeps the file."""
        self.write(repo, "placeholder")
        self.commit(repo, "add placeholder")
        self.open_pull_request(repo)
        entry = self.write(repo, "placeholder", url="https://other.org/catalog.json")
        self.commit(repo, "point placeholder at another catalog")
        git("switch", "-q", "main", cwd=repo)
        (repo / "placeholder.yaml").unlink()
        self.commit(repo, "remove placeholder")
        git("switch", "-q", "pr", cwd=repo)
        subprocess.run(["git", "merge", "-q", "main"], cwd=repo, capture_output=True)
        git("add", "placeholder.yaml", cwd=repo)
        git("commit", "-q", "--no-edit", cwd=repo)
        assert entry.exists()
        errors = self.run(repo, [entry], [entry])
        assert len(errors) == 1
        assert "belonged to another catalog" in errors[0]

    def test_a_draft_deleted_inside_the_pull_request_retires_nothing(self, repo):
        """Review finding: the PR adds a draft, deletes it, and adds it again
        with a corrected URL. The registry never held that ID."""
        self.open_pull_request(repo)
        self.write(repo, "draft", url="https://typo.org/catalog.json")
        self.commit(repo, "add draft")
        (repo / "draft.yaml").unlink()
        self.commit(repo, "remove draft")
        draft = self.write(repo, "draft")
        self.commit(repo, "add draft again")
        assert self.run(repo, [draft], [draft]) == []

    def test_without_a_base_ref_only_the_pattern_rule_runs(self, repo):
        self.registered_then_removed(repo, "placeholder")
        new = self.write(repo, "placeholder", url="https://other.org/catalog.json")
        assert self.run(repo, [new], [new], base_ref=None) == []
        bad = self.write(repo, "Bad_Name")
        assert len(self.run(repo, [bad], [bad], base_ref=None)) == 1

    def test_main_passes_the_base_ref_through(self, repo, monkeypatch):
        monkeypatch.setattr(validate_entries, "load_links", lambda p: {})
        monkeypatch.setattr(validate_entries, "load_state", lambda p: {})
        self.registered_then_removed(repo, "placeholder")
        new = self.write(repo, "placeholder", url="https://other.org/catalog.json")
        listing = repo.parent / "changed.txt"
        listing.write_text(f"{new}\n")
        args = [
            "--changed-file",
            str(listing),
            "--added-file",
            str(listing),
            "--catalog-dir",
            str(repo),
        ]
        assert validate_entries.main(args) == 0
        assert validate_entries.main([*args, "--base-ref", "main"]) == 1
