"""When an entry joined the registry, read from this repository's git history.

Nothing in a crawled catalog says when it was registered here, and the export
is rewritten wholesale on every run, so the commit that added
`catalogs/<id>.yaml` is the only durable record. This is the one module that
shells out; the crawler stays free of the working tree.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from registry.report import log


def first_registered(path: Path) -> str | None:
    """RFC 3339 timestamp of the commit that added `path`.

    Returns None when git cannot answer: outside a repository, in a shallow
    clone whose history stops short of the add, or for a file that has never
    been committed. A caller should fall back to the date already published
    rather than treat None as "registered just now".
    """
    # Run inside the entry's own directory. An absolute path handed to a git
    # invoked from somewhere else is "outside repository", and the registry's
    # scripts are also run from a checkout of a different repo in testing.
    if not path.parent.is_dir():
        return None

    try:
        completed = subprocess.run(
            [
                "git",
                "log",
                "--follow",
                "--diff-filter=A",
                "--format=%aI",
                "--",
                path.name,
            ],
            cwd=path.parent,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as e:
        log(f"  Warning: could not read the add date of {path}: {e}")
        return None

    if completed.returncode != 0:
        log(f"  Warning: git log failed for {path}: {completed.stderr.strip()}")
        return None

    # A renamed entry has one add per name under --follow, newest first. The
    # oldest is when the catalog joined the registry.
    dates = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    return dates[-1] if dates else None
