"""Reading an entry's registration date and its deletions out of git.

These build a throwaway repository rather than reading the registry's own
history, which changes with every commit.
"""

from __future__ import annotations

import os
import subprocess

import pytest

from registry.history import Deletion, _canonical, deletions, first_registered, url_at


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


class TestUrlAt:
    def test_reads_the_url_at_a_ref(self, repo):
        entry = repo / "catalogs" / "example.yaml"
        base = head(repo)
        entry.write_text("url: https://ex.org/moved/catalog.json\n")
        commit_all(repo, "move example")
        assert url_at(base, entry) == "https://ex.org/catalog.json"
        assert url_at("HEAD", entry) == "https://ex.org/moved/catalog.json"

    def test_a_file_absent_at_the_ref_has_none(self, repo):
        assert url_at("HEAD", repo / "catalogs" / "unseen.yaml") is None


class TestDeletions:
    def catalogs(self, repo):
        return repo / "catalogs"

    def test_a_history_without_deletions_is_empty(self, repo):
        assert deletions("HEAD", self.catalogs(repo)) == []

    def test_reads_the_url_the_deleted_file_held(self, repo):
        (repo / "catalogs" / "example.yaml").unlink()
        commit_all(repo, "remove example")
        assert deletions("HEAD", self.catalogs(repo)) == [
            Deletion(stem="example", commit=head(repo), url="https://ex.org/catalog.json")
        ]

    def test_lists_the_newest_deletion_first(self, repo):
        entry = repo / "catalogs" / "example.yaml"
        entry.unlink()
        commit_all(repo, "remove example")
        first = head(repo)
        entry.write_text("url: https://ex.org/second/catalog.json\n")
        commit_all(repo, "add example again")
        entry.unlink()
        commit_all(repo, "remove example again")
        found = deletions("HEAD", self.catalogs(repo))
        assert [(d.commit, d.url) for d in found] == [
            (head(repo), "https://ex.org/second/catalog.json"),
            (first, "https://ex.org/catalog.json"),
        ]

    def test_a_rename_counts_as_a_deletion_of_the_old_name(self, repo):
        """No --follow. The ID is the filename."""
        git("mv", "catalogs/example.yaml", "catalogs/renamed.yaml", cwd=repo)
        commit_all(repo, "rename example")
        assert [d.stem for d in deletions("HEAD", self.catalogs(repo))] == ["example"]

    def test_a_deleted_file_that_does_not_parse_has_no_url(self, repo):
        entry = repo / "catalogs" / "example.yaml"
        entry.write_text("url: [unclosed\n")
        commit_all(repo, "break example")
        entry.unlink()
        commit_all(repo, "remove example")
        assert deletions("HEAD", self.catalogs(repo))[0].url is None

    def test_only_entry_files_count(self, repo):
        nested = repo / "catalogs" / "drafts" / "nested.yaml"
        nested.parent.mkdir()
        nested.write_text("url: https://ex.org/nested/catalog.json\n")
        (repo / "catalogs" / "notes.md").write_text("notes\n")
        commit_all(repo, "add a nested file and notes")
        nested.unlink()
        (repo / "catalogs" / "notes.md").unlink()
        commit_all(repo, "remove them")
        assert deletions("HEAD", self.catalogs(repo)) == []

    def test_reads_only_the_history_of_the_ref(self, repo):
        """A deletion on another branch is not in the history of HEAD."""
        base = head(repo)
        git("switch", "-q", "-c", "side", cwd=repo)
        (repo / "catalogs" / "example.yaml").unlink()
        commit_all(repo, "remove example on side")
        assert deletions(base, self.catalogs(repo)) == []
        assert len(deletions("side", self.catalogs(repo))) == 1

    def test_finds_a_deletion_behind_a_merge_that_kept_the_file(self, repo):
        """A branch keeps a file that its base deleted, then merges the base.

        Git's default history simplification follows the branch side of the
        merge, where the file never changed, and hides the deletion.
        """
        git("branch", "-M", "main", cwd=repo)
        git("switch", "-q", "-c", "keeper", cwd=repo)
        entry = repo / "catalogs" / "example.yaml"
        entry.write_text("url: https://other.org/catalog.json\n")
        commit_all(repo, "point example at another catalog")
        git("switch", "-q", "main", cwd=repo)
        entry.unlink()
        commit_all(repo, "remove example")
        deleted_on_main = head(repo)
        git("switch", "-q", "keeper", cwd=repo)
        subprocess.run(["git", "merge", "-q", "main"], cwd=repo, capture_output=True)
        git("add", "catalogs/example.yaml", cwd=repo)
        git("commit", "-q", "--no-edit", cwd=repo)

        simplified = subprocess.run(
            ["git", "log", "--diff-filter=D", "--format=%H", "--", "example.yaml"],
            cwd=repo / "catalogs",
            check=True,
            capture_output=True,
            text=True,
        )
        assert simplified.stdout.strip() == ""
        assert [d.commit for d in deletions("HEAD", self.catalogs(repo))] == [deleted_on_main]

    def test_returns_empty_outside_a_repository(self, tmp_path):
        assert deletions("HEAD", tmp_path) == []
