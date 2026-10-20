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

import yaml

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


def _git(path: Path, *args: str) -> str | None:
    """Run Git in the directory of `path`. Return stdout, or `None` on failure.

    Tests may use entries in a separate repository. Run Git from the entry's
    directory so it reads that repository's history.
    """
    if not path.parent.is_dir():
        return None

    # The argument list is fixed and runs without a shell. git comes from PATH,
    # as it does for every other step of the workflows.
    try:
        completed = subprocess.run(  # nosec B603 B607
            ["git", *args],
            cwd=path.parent,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as e:
        log(f"  Warning: could not run Git for {path}: {e}")
        return None

    if completed.returncode != 0:
        log(f"  Warning: git {args[0]} failed for {path}: {completed.stderr.strip()}")
        return None
    return completed.stdout


def first_registered(path: Path) -> str | None:
    """Return the RFC 3339 timestamp of the commit that added `path`.

    If Git cannot find the commit, return `None`. This can happen outside a
    repository, in a shallow clone, or with an uncommitted file. If the
    result is `None`, use the previously published date as a fallback.
    """
    out = _git(path, "log", "--follow", "--diff-filter=A", "--format=%aI", "--", path.name)
    if out is None:
        return None

    # A renamed entry has one add per name under --follow, newest first. The
    # oldest is when the catalog joined the registry.
    dates = [line.strip() for line in out.splitlines() if line.strip()]
    return _canonical(dates[-1]) if dates else None


@dataclass(frozen=True)
class Deletion:
    """Store the most recent deletion commit and the entry's previous URL.

    If the deleted file had no URL or could not be parsed, `url` is `None`.
    """

    commit: str
    url: str | None


def last_deletion(path: Path) -> Deletion | None:
    """Return the most recent deletion of `path` in the history of `HEAD`.

    The path does not need to exist. Validation can read a deletion from the
    current pull request and recover the deleted entry's URL. If Git cannot
    find a deletion, return `None`. Omit `--follow` because each filename
    identifies a separate registry ID.
    """
    # Use --no-renames to count renames as deletions, as validation does.
    out = _git(path, "log", "--no-renames", "--diff-filter=D", "-1", "--format=%H", "--", path.name)
    commit = (out or "").strip()
    if not commit:
        return None

    # `./` makes the path relative to the entry's directory, where git runs.
    text = _git(path, "show", f"{commit}^:./{path.name}")
    try:
        entry = yaml.safe_load(text) if text else None
    except yaml.YAMLError:
        entry = None
    url = entry.get("url") if isinstance(entry, dict) else None
    return Deletion(commit=commit, url=url if isinstance(url, str) and url else None)
