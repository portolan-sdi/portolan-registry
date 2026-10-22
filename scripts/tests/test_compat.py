"""The contract compatibility check and the release script."""

from __future__ import annotations

import copy
import json
import shutil
import subprocess
from datetime import date
from pathlib import Path

import contract_compat
import pytest
import release_contract

from registry.compat import (
    MAJOR,
    MINOR,
    PATCH,
    classify,
    minimum_version,
    required_level,
    step,
)
from registry.contract import EXTENSION, SCHEMA_FILES, schema_url

REPO = Path(__file__).resolve().parents[2]
LINK = ("definitions", "link", "then", "properties")


def extension():
    return json.loads((REPO / "schema" / EXTENSION).read_text())


def link_fields(schema):
    node = schema
    for key in LINK:
        node = node[key]
    return node


def levels(old, new):
    return {(c.level, c.path.rsplit("/", 1)[-1]) for c in classify(old, new)}


class TestClassify:
    def test_no_change_is_no_change(self):
        assert classify(extension(), extension()) == []
        assert required_level([]) is None

    def test_a_new_version_url_is_no_change(self):
        new = json.loads(
            json.dumps(extension()).replace(
                schema_url(EXTENSION, None), schema_url(EXTENSION, (1, 0, 0))
            )
        )
        assert classify(extension(), new) == []

    def test_a_removed_field_is_major(self):
        new = extension()
        del link_fields(new)["portolan_registry:has_readme"]
        assert (MAJOR, "portolan_registry:has_readme") in levels(extension(), new)

    def test_a_new_optional_field_is_minor(self):
        new = extension()
        link_fields(new)["portolan_registry:new_field"] = {"type": "string"}
        assert classify(extension(), new)[0].level == MINOR
        assert required_level(classify(extension(), new)) == MINOR

    def test_a_description_change_is_a_patch(self):
        new = extension()
        link_fields(new)["portolan_registry:has_readme"]["description"] = "Changed."
        assert required_level(classify(extension(), new)) == PATCH

    def test_a_new_enum_value_is_major(self):
        new = extension()
        link_fields(new)["portolan_registry:status"]["enum"].append("archived")
        assert required_level(classify(extension(), new)) == MAJOR

    def test_a_field_that_becomes_nullable_is_major(self):
        new = extension()
        link_fields(new)["portolan_registry:has_readme"]["type"] = ["boolean", "null"]
        assert required_level(classify(extension(), new)) == MAJOR

    def test_a_field_that_is_no_longer_required_is_major(self):
        old = extension()
        new = copy.deepcopy(old)
        new["definitions"]["link"]["then"]["required"].remove("portolan_registry:has_readme")
        assert (MAJOR, "portolan_registry:has_readme") in levels(old, new)

    def test_a_new_constraint_is_major(self):
        new = extension()
        link_fields(new)["portolan_registry:collection_count"]["maximum"] = 10
        assert required_level(classify(extension(), new)) == MAJOR

    def test_a_new_definition_is_minor(self):
        new = extension()
        new["definitions"]["unused"] = {"type": "string"}
        assert required_level(classify(extension(), new)) == MINOR

    @pytest.mark.parametrize(
        ("old", "new", "expected"),
        [
            ((0, 1, 0), (0, 2, 0), MAJOR),
            ((0, 1, 0), (0, 1, 1), MINOR),
            ((0, 1, 3), (1, 0, 0), MAJOR),
            ((1, 0, 0), (2, 0, 0), MAJOR),
            ((1, 0, 0), (1, 1, 0), MINOR),
            ((1, 0, 0), (1, 0, 1), PATCH),
            ((1, 0, 0), (1, 0, 0), None),
            ((1, 2, 0), (1, 1, 9), None),
        ],
    )
    def test_step(self, old, new, expected):
        assert step(old, new) == expected


@pytest.mark.parametrize(
    ("old", "level", "expected"),
    [
        ((0, 1, 2), MAJOR, (0, 2, 0)),
        ((0, 1, 2), MINOR, (0, 1, 3)),
        ((0, 1, 2), PATCH, (0, 1, 3)),
        ((1, 2, 3), MAJOR, (2, 0, 0)),
        ((1, 2, 3), MINOR, (1, 3, 0)),
        ((1, 2, 3), PATCH, (1, 2, 4)),
    ],
)
def test_minimum_version(old, level, expected):
    assert minimum_version(old, level) == expected


class Repo:
    """A copy of the contract files, ready to release 0.1.0."""

    def __init__(self, root: Path) -> None:
        self.root = root
        shutil.copytree(REPO / "schema", root / "schema")
        self.changelog("## [0.1.0] - Unreleased")

    def changelog(self, *headings: str) -> None:
        text = "# Contract\n\n" + "\n\n".join(headings) + "\n"
        (self.root / "schema" / "CHANGELOG.md").write_text(text)

    def release(self, version: str = "0.1.0") -> None:
        release_contract.release(version, root=self.root, today=date(2026, 10, 22))

    def edit(self, change) -> None:
        path = self.root / "schema" / EXTENSION
        schema = json.loads(path.read_text())
        change(schema)
        path.write_text(json.dumps(schema))

    def check(self):
        return contract_compat.check(root=self.root)


@pytest.fixture
def repo(tmp_path):
    return Repo(tmp_path)


class TestCheck:
    def test_passes_before_the_first_release(self, repo):
        assert repo.check() == []

    def test_passes_right_after_a_release(self, repo):
        repo.release()
        assert repo.check() == []

    def test_an_additive_change_with_a_patch_heading_passes(self, repo):
        repo.release()
        repo.changelog("## [0.1.1] - Unreleased", "## [0.1.0] - 2026-10-22")
        repo.edit(
            lambda s: link_fields(s).update({"portolan_registry:new_field": {"type": "string"}})
        )
        assert repo.check() == []

    def test_a_removed_field_under_a_patch_heading_fails_and_names_it(self, repo):
        repo.release()
        repo.changelog("## [0.1.1] - Unreleased", "## [0.1.0] - 2026-10-22")
        repo.edit(lambda s: link_fields(s).pop("portolan_registry:has_readme"))
        [problem] = repo.check()
        assert "is breaking, so it needs version 0.2.0 or later" in problem
        assert "portolan_registry:has_readme" in problem

    def test_a_removed_field_under_a_new_minor_passes(self, repo):
        repo.release()
        repo.changelog("## [0.2.0] - Unreleased", "## [0.1.0] - 2026-10-22")
        repo.edit(lambda s: link_fields(s).pop("portolan_registry:has_readme"))
        assert repo.check() == []

    def test_from_1_0_a_breaking_change_needs_a_new_major(self, repo):
        repo.changelog("## [1.0.0] - Unreleased")
        repo.release("1.0.0")
        repo.changelog("## [1.1.0] - Unreleased", "## [1.0.0] - 2026-10-22")
        repo.edit(lambda s: link_fields(s).pop("portolan_registry:has_readme"))
        [problem] = repo.check()
        assert "needs version 2.0.0 or later" in problem

    def test_from_1_0_an_additive_change_needs_a_new_minor(self, repo):
        repo.changelog("## [1.0.0] - Unreleased")
        repo.release("1.0.0")
        repo.changelog("## [1.0.1] - Unreleased", "## [1.0.0] - 2026-10-22")
        repo.edit(lambda s: link_fields(s).update({"portolan_registry:new_field": {}}))
        [problem] = repo.check()
        assert "is additive, so it needs version 1.1.0 or later" in problem

    def test_a_change_without_an_unreleased_heading_fails(self, repo):
        repo.release()
        repo.edit(lambda s: link_fields(s).update({"portolan_registry:new_field": {}}))
        [problem] = repo.check()
        assert "Unreleased" in problem

    def test_an_unreleased_version_must_be_later(self, repo):
        repo.release()
        repo.changelog("## [0.1.0] - Unreleased")
        [problem] = repo.check()
        assert "must be later" in problem

    def test_a_dated_heading_needs_its_release(self, repo):
        repo.release()
        repo.changelog("## [0.1.1] - 2026-11-01", "## [0.1.0] - 2026-10-22")
        [problem] = repo.check()
        assert "release_contract.py" in problem

    def test_a_dated_heading_must_be_the_latest_release(self, repo):
        repo.release()
        repo.changelog("## [0.0.9] - 2026-01-01")
        (repo.root / "schemas" / "v0.0.9").mkdir()
        [problem] = repo.check()
        assert "latest release is 0.1.0" in problem


def git(root, *args):
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


class TestReleasedFilesStayFixed:
    @pytest.fixture
    def history(self, repo):
        repo.release()
        git(repo.root, "init", "-q", "-b", "main")
        git(repo.root, "add", ".")
        git(repo.root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base")
        git(repo.root, "checkout", "-qb", "change")
        return repo

    def commit(self, repo):
        git(repo.root, "add", "-A")
        git(repo.root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "change")

    def test_a_new_release_directory_passes(self, history):
        history.changelog("## [0.1.1] - Unreleased", "## [0.1.0] - 2026-10-22")
        history.edit(lambda s: link_fields(s)["portolan_registry:id"].update({"description": "x"}))
        release_contract.release("0.1.1", root=history.root)
        self.commit(history)
        assert contract_compat.check(root=history.root, base_ref="main") == []

    def test_an_edited_release_fails(self, history):
        path = history.root / "schemas" / "v0.1.0" / EXTENSION
        path.write_text(path.read_text().replace("Portolan Registry Extension", "Changed"))
        self.commit(history)
        [problem] = contract_compat.check(root=history.root, base_ref="main")
        assert "must never change" in problem
        assert "schemas/v0.1.0/extension.json" in problem

    def test_a_deleted_release_fails(self, history):
        (history.root / "schemas" / "v0.1.0" / EXTENSION).unlink()
        self.commit(history)
        problems = contract_compat.check(root=history.root, base_ref="main")
        assert any("must never change" in p for p in problems)


class TestRelease:
    def test_writes_versioned_copies_and_dates_the_changelog(self, repo):
        directory = release_contract.release("0.1.0", root=repo.root, today=date(2026, 10, 22))
        assert directory == repo.root / "schemas" / "v0.1.0"
        for name in SCHEMA_FILES:
            text = (directory / name).read_text()
            assert schema_url(name, (0, 1, 0)) in text
            assert schema_url(name, None) not in text
        assert "## [0.1.0] - 2026-10-22" in (repo.root / "schema" / "CHANGELOG.md").read_text()

    def test_refuses_a_version_the_changelog_does_not_name(self, repo):
        with pytest.raises(release_contract.ReleaseRefused, match="top heading"):
            release_contract.release("0.2.0", root=repo.root)

    def test_refuses_to_overwrite_a_release(self, repo):
        (repo.root / "schemas" / "v0.1.0").mkdir(parents=True)
        with pytest.raises(release_contract.ReleaseRefused, match="already exists"):
            release_contract.release("0.1.0", root=repo.root)

    def test_cli_reports_a_refusal(self, repo, monkeypatch, capsys):
        monkeypatch.chdir(repo.root)
        assert release_contract.main(["0.2.0"]) == 1
        assert "Release refused" in capsys.readouterr().err
        assert release_contract.main(["0.1.0"]) == 0

    def test_compat_cli_exit_codes(self, repo, monkeypatch, capsys):
        monkeypatch.chdir(repo.root)
        assert contract_compat.main([]) == 0
        repo.release()
        repo.changelog("## [0.1.1] - Unreleased", "## [0.1.0] - 2026-10-22")
        repo.edit(lambda s: link_fields(s).pop("portolan_registry:has_readme"))
        assert contract_compat.main([]) == 1
        assert "FAIL" in capsys.readouterr().err
