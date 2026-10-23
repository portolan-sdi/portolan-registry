#!/usr/bin/env python3
"""Release the working contract schemas as one fixed version.

Run from the repository root, on a branch:

    uv run --frozen scripts/release_contract.py 0.1.0

The script copies schema/extension.json and schema/coverage.json to
schemas/vX.Y.Z/ with their versioned URLs, and dates the CHANGELOG heading.
Open a pull request with the result. When it merges, the Pages deploy writes
the vX/ tree and the tag workflow tags the commit as export-vX.Y.Z.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, date, datetime
from pathlib import Path

from registry.contract import (
    CHANGELOG_PATH,
    SCHEMA_FILES,
    UNRELEASED,
    changelog_head,
    format_version,
    parse_version,
    schema_path,
    schema_url,
)
from registry.report import log


class ReleaseRefused(Exception):
    """Raised instead of writing a release that breaks the release rules."""


def release(version_text: str, *, root: Path = Path("."), today: date | None = None) -> Path:
    """Write the release. Returns its directory."""
    version = parse_version(version_text)
    target, is_released = changelog_head(root / CHANGELOG_PATH)
    if is_released or target != version:
        raise ReleaseRefused(
            f"the CHANGELOG top heading must be '## [{format_version(version)}] - {UNRELEASED}'"
        )
    directory = schema_path(SCHEMA_FILES[0], version, root=root).parent
    if directory.exists():
        raise ReleaseRefused(f"{directory} already exists")

    directory.mkdir(parents=True)
    for name in SCHEMA_FILES:
        text = schema_path(name, None, root=root).read_text()
        text = text.replace(schema_url(name, None), schema_url(name, version))
        # The copy must still be a JSON document after the URL change.
        json.loads(text)
        (directory / name).write_text(text)

    today = today or datetime.now(UTC).date()
    changelog = root / CHANGELOG_PATH
    old = f"## [{format_version(version)}] - {UNRELEASED}"
    changelog.write_text(
        changelog.read_text().replace(
            old, f"## [{format_version(version)}] - {today.isoformat()}", 1
        )
    )
    return directory


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", help="The version to release, for example 0.1.0.")
    args = parser.parse_args(argv)
    try:
        directory = release(args.version)
    except (ReleaseRefused, ValueError) as e:
        log(f"Release refused: {e}")
        return 1
    log(f"Wrote {directory}. Open a pull request with it and schema/CHANGELOG.md.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
