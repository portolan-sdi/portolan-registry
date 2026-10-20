"""What this repository's git history records about an entry file.

Nothing in a crawled catalog says when it was registered here, and the export
is rewritten wholesale on every run, so the commit that added
`catalogs/<id>.yaml` is the only durable record. The same history records
every entry that left the registry, which is how the gate keeps an id from
naming a second catalog (#242). This is the one module that shells out; the
crawler stays free of the working tree.
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
    """Run git in the directory of `path`. Returns stdout, or None on failure.

    Run inside the entry's own directory. An absolute path handed to a git
    invoked from somewhere else is "outside repository", and the registry's
    scripts are also run from a checkout of a different repo in testing.
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
        log(f"  Warning: could not run git for {path}: {e}")
        return None

    if completed.returncode != 0:
        log(f"  Warning: git {args[0]} failed for {path}: {completed.stderr.strip()}")
        return None
    return completed.stdout


def first_registered(path: Path) -> str | None:
    """RFC 3339 timestamp of the commit that added `path`.

    Returns None when git cannot answer: outside a repository, in a shallow
    clone whose history stops short of the add, or for a file that has never
    been committed. A caller should fall back to the date already published
    rather than treat None as "registered just now".
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
    """The newest commit that deleted an entry file, and the URL the file held.

    `url` is None when the deleted file named no URL or could not be parsed.
    """

    commit: str
    url: str | None


def last_deletion(path: Path) -> Deletion | None:
    """The newest deletion of `path` in the history of HEAD, if there is one.

    The path need not exist. A pull request that deletes an entry commits the
    deletion, so the gate finds it here too, with the URL the file held.
    Returns None when git cannot answer or the path was never deleted. No
    --follow: an id is the file name, so a deletion of another name is about
    another id.
    """
    # --no-renames, so a rename counts as the deletion of the old name, as it
    # does in the gate's own listing.
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
