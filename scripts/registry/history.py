"""Read an entry's registration and deletion history from Git.

The commit that added `catalogs/<id>.yaml` records when a catalog joined the
registry. The catalog itself has no registration date, and each run rewrites
the export. Git history also records deleted entries so validation can
prevent ID reuse (#242). This module runs Git commands so the crawler does
not need access to a working tree.
"""

from __future__ import annotations

# bandit B404: this module runs git with a fixed argument list.
import subprocess  # nosec B404
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from registry.entries import deprecation
from registry.report import log


def _canonical(stamp: str) -> str:
    """Render a git timestamp the way the rest of the registry writes RFC 3339.

    Git spells a zero UTC offset as `Z` in recent versions and as `+00:00` in
    older ones. Both are RFC 3339, so pin the spelling that `datetime.isoformat`
    produces everywhere else in the registry. Without this the export rewrites
    every entry the day a runner's git changes, which is the spurious-diff
    churn issue #52 removed. A non-UTC offset is left as the author wrote it.
    """
    try:
        return datetime.fromisoformat(stamp).isoformat()
    except ValueError:
        return stamp


def _git(cwd: Path, *args: str) -> str | None:
    """Run Git in the directory `cwd`. Return stdout, or `None` on failure.

    Tests may use entries in a separate repository. Run Git from the entry's
    directory so it reads that repository's history.
    """
    if not cwd.is_dir():
        return None

    # The argument list is fixed and runs without a shell. git comes from PATH,
    # as it does for every other step of the workflows.
    try:
        completed = subprocess.run(  # nosec B603 B607
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as e:
        log(f"  Warning: could not run Git in {cwd}: {e}")
        return None

    if completed.returncode != 0:
        log(f"  Warning: git {args[0]} failed in {cwd}: {completed.stderr.strip()}")
        return None
    return completed.stdout


def first_registered(path: Path) -> str | None:
    """Return the RFC 3339 timestamp of the commit that added `path`.

    If Git cannot find the commit, return `None`. This can happen outside a
    repository, in a shallow clone, or with an uncommitted file. If the
    result is `None`, use the previously published date as a fallback.
    """
    out = _git(path.parent, "log", "--follow", "--diff-filter=A", "--format=%aI", "--", path.name)
    if out is None:
        return None

    # A renamed entry has one add per name under --follow, newest first. The
    # oldest is when the catalog joined the registry.
    dates = [line.strip() for line in out.splitlines() if line.strip()]
    return _canonical(dates[-1]) if dates else None


def _entry_url(text: str | None) -> str | None:
    """Return the `url` field of an entry file's text, or `None`."""
    try:
        entry = yaml.safe_load(text) if text else None
    except yaml.YAMLError:
        return None
    url = entry.get("url") if isinstance(entry, dict) else None
    return url if isinstance(url, str) and url else None


def entry_at(ref: str, path: Path) -> dict[str, Any] | None:
    """Return the entry at `path` from Git revision `ref`.

    Return `None` if the entry is missing, contains invalid YAML, is not a
    mapping, or cannot be read from Git.
    """
    # `./` makes the path relative to the entry's directory, where Git runs.
    text = _git(path.parent, "show", f"{ref}:./{path.name}")
    try:
        entry = yaml.safe_load(text) if text else None
    except yaml.YAMLError:
        return None
    return entry if isinstance(entry, dict) else None


def url_at(ref: str, path: Path) -> str | None:
    """Return the URL that the entry at `path` held at `ref`.

    If the entry did not exist at `ref`, or Git cannot read it, return `None`.
    """
    url = (entry_at(ref, path) or {}).get("url")
    return url if isinstance(url, str) and url else None


def deprecated_since(path: Path) -> str | None:
    """Return the RFC 3339 timestamp of the commit that deprecated `path`.

    Parse the entry at each commit that changed the file, newest first. The
    date is the oldest commit of the newest unbroken run of commits where
    the entry is deprecated. A text match on the `deprecated:` line is not
    enough: a comment on the line, or a quoted key, would move or lose the
    date without a change of value.

    Walk with `--first-parent`, so the date is when the deprecation reached
    the branch, not when a side branch drafted it.

    If the newest commit does not deprecate the entry, or Git cannot answer,
    return `None`. The caller keeps the previously published date, as with
    `first_registered`.
    """
    out = _git(path.parent, "log", "--first-parent", "--format=%H %aI", "--", path.name)
    since = None
    for line in (out or "").splitlines():
        commit, _, stamp = line.strip().partition(" ")
        if not commit or not deprecation(entry_at(commit, path) or {}).deprecated:
            break
        since = stamp
    return _canonical(since) if since else None


@dataclass(frozen=True)
class Deletion:
    """Store one commit that deleted an entry file, and the URL it held.

    If the deleted file had no URL or could not be parsed, `url` is `None`.
    """

    stem: str
    commit: str
    url: str | None


def deletions(ref: str, catalog_dir: Path) -> list[Deletion]:
    """Return every deletion of an entry file in the history of `ref`.

    Results are newest first. Pass the base branch, not the pull request
    head. Validation asks whether the registry ever held an ID, and a draft
    commit inside the pull request does not register anything.

    Use `--full-history`. Git's default history simplification follows one
    parent of a merge when that parent leaves a path unchanged. A branch that
    keeps a file and then merges a branch that deleted it would hide the
    deletion. Use `--no-renames` so that a rename counts as the deletion of
    the old name, as it does in validation.
    """
    out = _git(
        catalog_dir,
        "log",
        "--full-history",
        "--no-renames",
        "--diff-filter=D",
        "--relative",
        "--name-only",
        "--format=%x00%H",
        ref,
        "--",
        ".",
    )
    if not out:
        return []

    found = []
    for record in out.split("\0"):
        lines = [line.strip() for line in record.splitlines() if line.strip()]
        if not lines:
            continue
        commit, names = lines[0], lines[1:]
        for name in names:
            # Only entry files directly under catalog_dir are registry IDs.
            if "/" in name or not name.endswith(".yaml"):
                continue
            text = _git(catalog_dir, "show", f"{commit}^:./{name}")
            found.append(Deletion(stem=name[: -len(".yaml")], commit=commit, url=_entry_url(text)))
    return found
