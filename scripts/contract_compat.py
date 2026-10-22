#!/usr/bin/env python3
"""Check that a change to the contract schemas names the right version.

Run from the repository root:

    uv run --frozen scripts/contract_compat.py
    uv run --frozen scripts/contract_compat.py --base-ref origin/main

The check compares the working schemas in schema/ with the latest release in
schemas/. It fails when the change needs a larger version step than the top
heading of schema/CHANGELOG.md names. A breaking change must merge as its
own release, so the check also fails on a breaking change under an
Unreleased heading. With --base-ref, it also fails when the branch changes
or deletes a released schema. A release never changes.
"""

from __future__ import annotations

import argparse

# bandit B404: the check runs git without a shell.
import subprocess  # nosec B404
from pathlib import Path

from registry.compat import (
    LEVEL_NAMES,
    MAJOR,
    MINOR,
    Change,
    classify,
    minimum_version,
    required_level,
    step,
)
from registry.contract import (
    CHANGELOG_PATH,
    RELEASED_DIR,
    SCHEMA_FILES,
    Version,
    changelog_head,
    format_version,
    load_schema,
    released_versions,
)
from registry.report import log


def released_files_changed(base_ref: str, *, root: Path = Path(".")) -> list[str]:
    """Each released schema file that the branch changes, renames, or deletes."""
    proc = subprocess.run(  # nosec B603 B607
        ["git", "diff", "--name-status", f"{base_ref}...HEAD", "--", f"{RELEASED_DIR}/"],
        capture_output=True,
        text=True,
        check=True,
        cwd=root,
    )
    return [line for line in proc.stdout.splitlines() if line and not line.startswith("A")]


GUIDE = "schema/README.md"


def schema_changes(
    old: Version | None, new: Version | None, *, root: Path = Path(".")
) -> list[Change]:
    """Every change from release `old` to release `new`. None is the working copy."""
    changes: list[Change] = []
    for name in SCHEMA_FILES:
        before = load_schema(name, old, root=root)
        after = load_schema(name, new, root=root)
        changes.extend(
            Change(c.level, f"{name}{c.path}", c.detail) for c in classify(before, after)
        )
    return changes


def _blocking(changes: list[Change], allowed: int) -> str:
    return "\n  " + "\n  ".join(str(c) for c in changes if c.level > allowed)


def _too_small(changes: list[Change], old: Version, new: Version) -> str | None:
    """The problem when version `new` is too small a step after `old` for `changes`."""
    needed = required_level(changes)
    named = step(old, new)
    if named is None or needed is None or named >= needed:
        return None
    return (
        f"this change is {LEVEL_NAMES[needed]}, so it needs version "
        f"{format_version(minimum_version(old, needed))} or later. The CHANGELOG names "
        f"{format_version(new)}, which allows only a {LEVEL_NAMES[named]} change after "
        f"{format_version(old)}:" + _blocking(changes, named)
    )


def _released(target: Version, releases: list[Version], *, root: Path) -> list[str]:
    """The problems when the top CHANGELOG heading has a date."""
    latest = releases[-1]
    if target not in releases:
        return [
            f"the CHANGELOG dates {format_version(target)}, but schemas/v{format_version(target)} "
            "does not exist. Run scripts/release_contract.py."
        ]
    if target != latest:
        return [
            f"the CHANGELOG top heading is {format_version(target)}, "
            f"but the latest release is {format_version(latest)}"
        ]
    problems = []
    if schema_changes(latest, None, root=root):
        problems.append(
            f"the working schemas differ from release {format_version(latest)}. "
            "Add a '## [X.Y.Z] - Unreleased' heading to schema/CHANGELOG.md."
        )
    if len(releases) > 1:
        # A breaking change merges as its own release, so the release itself
        # must name a large enough step after the release before it.
        previous = releases[-2]
        changes = schema_changes(previous, latest, root=root)
        for change in changes:
            log(str(change))
        problem = _too_small(changes, previous, latest)
        if problem:
            problems.append(f"release {format_version(latest)}: {problem}")
    return problems


def _unreleased(target: Version, releases: list[Version], *, root: Path) -> list[str]:
    """The problems when the top CHANGELOG heading says Unreleased."""
    latest = releases[-1]
    changes = schema_changes(latest, None, root=root)
    for change in changes:
        log(str(change))
    if step(latest, target) is None:
        return [
            f"the unreleased version {format_version(target)} must be later than "
            f"the latest release {format_version(latest)}"
        ]
    if required_level(changes) == MAJOR:
        # The store has one shape. A breaking change on main before its
        # release would break the tree of the current line, and the deploy.
        return [
            "this change is breaking, so it needs version "
            f"{format_version(minimum_version(latest, MAJOR))} or later, and it must merge "
            "as that release. Name the version in the top heading of schema/CHANGELOG.md, "
            "then run scripts/release_contract.py in this pull request:" + _blocking(changes, MINOR)
        ]
    problem = _too_small(changes, latest, target)
    return [problem] if problem else []


def check(*, root: Path = Path("."), base_ref: str | None = None) -> list[str]:
    """Every problem with the contract on this tree. Empty when it passes."""
    if base_ref:
        problems = [
            f"a released schema must never change: {line}"
            for line in released_files_changed(base_ref, root=root)
        ]
        if problems:
            # A comparison against a changed release would mislead.
            return problems

    target, is_released = changelog_head(root / CHANGELOG_PATH)
    releases = released_versions(root / RELEASED_DIR)
    if not releases:
        log(f"No contract release yet. The CHANGELOG names {format_version(target)}.")
        return []
    if is_released:
        return _released(target, releases, root=root)
    return _unreleased(target, releases, root=root)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-ref",
        help="Also fail when the branch changes a released schema relative to this ref.",
    )
    args = parser.parse_args(argv)
    try:
        problems = check(base_ref=args.base_ref)
    except ValueError as e:
        problems = [str(e)]
    for problem in problems:
        log(f"FAIL: {problem}")
    if problems:
        log(f"See {GUIDE} for the version rules and the release steps.")
    else:
        log("The contract version matches the schema change.")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
