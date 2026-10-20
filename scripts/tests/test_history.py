"""Reading an entry's registration date and its deletions out of git.

These build a throwaway repository rather than reading the registry's own
history, which changes with every commit.
"""

from __future__ import annotations

import os
import subprocess

import pytest

from registry.history import Deletion, _canonical, first_registered, last_deletion


def git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    """A repository with one committed entry, added at a known time."""
    git("init", "-q", cwd=tmp_path)
    git("config", "user.email", "test@example.org", cwd=tmp_path)
    git("config", "user.name", "Test", cwd=tmp_path)
    git("config", "commit.gpgsign", "false", cwd=tmp_path)

    entry = tmp_path / "catalogs" / "example.yaml"
    entry.parent.mkdir()
    entry.write_text("url: https://ex.org/catalog.json\n")
    git("add", "catalogs/example.yaml", cwd=tmp_path)
    subprocess.run(
        ["git", "commit", "-q", "-m", "add example"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        env={
            **os.environ,
            "GIT_AUTHOR_DATE": "2026-03-04T09:00:00+00:00",
            "GIT_COMMITTER_DATE": "2026-03-04T09:00:00+00:00",
        },
    )
    return tmp_path


def test_reads_the_add_commit(repo):
    assert first_registered(repo / "catalogs" / "example.yaml") == ("2026-03-04T09:00:00+00:00")


def test_spells_a_utc_offset_the_way_the_export_does():
    """Recent git writes `Z`, older git writes `+00:00`; the export needs one.

    Asserted on the raw strings so the test fails on every git, not only on the
    versions whose spelling changed.
    """
    assert _canonical("2026-03-04T09:00:00Z") == "2026-03-04T09:00:00+00:00"
    assert _canonical("2026-03-04T09:00:00+00:00") == "2026-03-04T09:00:00+00:00"


def test_keeps_an_authors_own_offset():
    """Published entries carry non-UTC offsets; rewriting them churns the export."""
    assert _canonical("2026-06-09T10:55:36+02:00") == "2026-06-09T10:55:36+02:00"


def test_passes_through_a_timestamp_it_cannot_parse():
    assert _canonical("not a date") == "not a date"


def test_reports_the_oldest_add_after_a_later_edit(repo):
    entry = repo / "catalogs" / "example.yaml"
    entry.write_text("url: https://ex.org/moved/catalog.json\n")
    git("add", "catalogs/example.yaml", cwd=repo)
    git("commit", "-q", "-m", "move example", cwd=repo)
    assert first_registered(entry).startswith("2026-03-04")


def test_returns_none_for_an_uncommitted_file(repo):
    fresh = repo / "catalogs" / "unregistered.yaml"
    fresh.write_text("url: https://ex.org/other/catalog.json\n")
    assert first_registered(fresh) is None


def test_returns_none_outside_a_repository(tmp_path):
    """A caller keeps the date already published rather than stamping today."""
    loose = tmp_path / "example.yaml"
    loose.write_text("url: https://ex.org/catalog.json\n")
    assert first_registered(loose) is None


def commit_all(repo, message):
    git("add", "-A", cwd=repo)
    git("commit", "-q", "-m", message, cwd=repo)


def head(repo):
    out = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    )
    return out.stdout.strip()


class TestLastDeletion:
    def test_a_file_never_deleted_has_none(self, repo):
        assert last_deletion(repo / "catalogs" / "example.yaml") is None

    def test_a_path_never_committed_has_none(self, repo):
        assert last_deletion(repo / "catalogs" / "unseen.yaml") is None

    def test_reads_the_url_the_deleted_file_held(self, repo):
        entry = repo / "catalogs" / "example.yaml"
        entry.unlink()
        commit_all(repo, "remove example")
        assert last_deletion(entry) == Deletion(
            commit=head(repo), url="https://ex.org/catalog.json"
        )

    def test_reports_the_newest_deletion(self, repo):
        entry = repo / "catalogs" / "example.yaml"
        entry.unlink()
        commit_all(repo, "remove example")
        entry.write_text("url: https://ex.org/second/catalog.json\n")
        commit_all(repo, "add example again")
        entry.unlink()
        commit_all(repo, "remove example again")
        gone = last_deletion(entry)
        assert gone.commit == head(repo)
        assert gone.url == "https://ex.org/second/catalog.json"

    def test_a_deletion_of_another_name_does_not_count(self, repo):
        """No --follow: the id is the file name."""
        git("mv", "catalogs/example.yaml", "catalogs/renamed.yaml", cwd=repo)
        commit_all(repo, "rename example")
        assert last_deletion(repo / "catalogs" / "renamed.yaml") is None
        assert (
            last_deletion(repo / "catalogs" / "example.yaml").url == "https://ex.org/catalog.json"
        )

    def test_a_deleted_file_that_does_not_parse_has_no_url(self, repo):
        entry = repo / "catalogs" / "example.yaml"
        entry.write_text("url: [unclosed\n")
        commit_all(repo, "break example")
        entry.unlink()
        commit_all(repo, "remove example")
        gone = last_deletion(entry)
        assert gone is not None
        assert gone.url is None

    def test_returns_none_outside_a_repository(self, tmp_path):
        assert last_deletion(tmp_path / "example.yaml") is None
