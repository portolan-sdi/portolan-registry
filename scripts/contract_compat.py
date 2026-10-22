#!/usr/bin/env python3
"""Check that a change to the contract schemas names the right version.

Run from the repository root:

    uv run --frozen scripts/contract_compat.py
    uv run --frozen scripts/contract_compat.py --base-ref origin/main

The check compares the working schemas in schema/ with the latest release in
schemas/. It fails when the change needs a larger version step than the top
heading of schema/CHANGELOG.md names. With --base-ref, it also fails when the
branch changes or deletes a released schema. A release never changes.
"""

from __future__ import annotations

import argparse

# bandit B404: the check runs git without a shell.
import subprocess  # nosec B404
from pathlib import Path

from registry.compat import (
    LEVEL_NAMES,
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


def schema_changes(base: tuple[int, int, int], *, root: Path = Path(".")) -> list[Change]:
    changes: list[Change] = []
    for name in SCHEMA_FILES:
        old = load_schema(name, base, root=root)
        new = load_schema(name, None, root=root)
        changes.extend(Change(c.level, f"{name}{c.path}", c.detail) for c in classify(old, new))
    return changes


def check(*, root: Path = Path("."), base_ref: str | None = None) -> list[str]:
    """Every problem with the contract on this tree. Empty when it passes."""
    problems = []
    if base_ref:
        problems += [
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
        return problems
    latest = releases[-1]

    changes = schema_changes(latest, root=root)
    for change in changes:
        log(str(change))

    if is_released:
        if target not in releases:
            problems.append(
                f"the CHANGELOG dates {format_version(target)}, but schemas/v{format_version(target)} "
                "does not exist. Run scripts/release_contract.py."
            )
        elif target != latest:
            problems.append(
                f"the CHANGELOG top heading is {format_version(target)}, "
                f"but the latest release is {format_version(latest)}"
            )
        elif changes:
            problems.append(
                f"the working schemas differ from release {format_version(latest)}. "
                "Add a '## [X.Y.Z] - Unreleased' heading to schema/CHANGELOG.md."
            )
        return problems

    needed = required_level(changes)
    named = step(latest, target)
    if named is None:
        problems.append(
            f"the unreleased version {format_version(target)} must be later than "
            f"the latest release {format_version(latest)}"
        )
    elif needed is not None and named < needed:
        blocking = [str(c) for c in changes if c.level > named]
        problems.append(
            f"this change is {LEVEL_NAMES[needed]}, so it needs version "
            f"{format_version(minimum_version(latest, needed))} or later. The CHANGELOG names "
            f"{format_version(target)}, which allows only a {LEVEL_NAMES[named]} change after "
            f"{format_version(latest)}:\n  " + "\n  ".join(blocking)
        )
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-ref",
        help="Also fail when the branch changes a released schema relative to this ref.",
    )
    args = parser.parse_args(argv)
    problems = check(base_ref=args.base_ref)
    for problem in problems:
        log(f"FAIL: {problem}")
    if not problems:
        log("The contract version matches the schema change.")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
