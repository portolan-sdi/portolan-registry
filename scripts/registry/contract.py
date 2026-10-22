"""The versioned registry contract that registry.portolan-sdi.org publishes.

The git store, exports/catalogs.json, holds the registry state in the shape
that the crawler writes. This module turns that store into the published
shape. The published shape follows the extension schema of one contract
version. `next/` follows the working schema in schema/. Each release line
has its own tree, which follows the latest released schema of that line.

A release line is the set of versions that are compatible with each other.
From 1.0.0 on, the major version names the line, and the tree is `v1/`.
Before 1.0.0, the minor version names the line, and the tree is `v0.1/`.
This is the rule that Cargo applies to 0.x versions.
"""

from __future__ import annotations

import calendar
import copy
import json
import re
from collections.abc import Iterator, Mapping
from datetime import date
from pathlib import Path
from typing import Any

from registry.export import PREFIX

SITE_URL = "https://registry.portolan-sdi.org"

SCHEMA_DIR = Path("schema")
RELEASED_DIR = Path("schemas")
CHANGELOG_PATH = SCHEMA_DIR / "CHANGELOG.md"

# The two schemas of the contract. Both share one version number.
EXTENSION = "extension.json"
COVERAGE = "coverage.json"
SCHEMA_FILES = (EXTENSION, COVERAGE)

NEXT = "next"

# After the first release of a new line, the build writes the previous line
# for this many months. The release date comes from schema/CHANGELOG.md.
DEPRECATION_MONTHS = 6

Version = tuple[int, int, int]

# (major,) from 1.0.0 on, (0, minor) before it.
Line = tuple[int, ...]

_VERSION_DIR = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")

# Store fields that the published shape renames. The published names carry
# the prefix, because the extension defines them.
ROOT_RENAMES = {"generated": PREFIX + "generated", "count": PREFIX + "count"}


def parse_version(text: str) -> Version:
    """Read "1.2.3" or "v1.2.3" as a version tuple."""
    match = _VERSION_DIR.match(text if text.startswith("v") else f"v{text}")
    if not match:
        raise ValueError(f"not a version: {text!r}")
    major, minor, patch = (int(part) for part in match.groups())
    return major, minor, patch


def format_version(version: Version) -> str:
    return ".".join(str(part) for part in version)


def released_versions(released_dir: Path = RELEASED_DIR) -> list[Version]:
    """Every released contract version, oldest first."""
    if not released_dir.is_dir():
        return []
    return sorted(
        parse_version(path.name)
        for path in released_dir.iterdir()
        if path.is_dir() and _VERSION_DIR.match(path.name)
    )


def line_of(version: Version) -> Line:
    """The release line of a version. Versions in one line are compatible."""
    return (0, version[1]) if version[0] == 0 else (version[0],)


def line_name(line: Line) -> str:
    """The tree of a release line, such as `v1` or `v0.1`."""
    return "v" + ".".join(str(part) for part in line)


def latest_releases(released_dir: Path = RELEASED_DIR) -> dict[Line, Version]:
    """The latest released version of each release line."""
    latest: dict[Line, Version] = {}
    for version in released_versions(released_dir):
        latest[line_of(version)] = version
    return latest


_HEADING = re.compile(r"^## \[(\d+\.\d+\.\d+)\] - (.+?)\s*$")

UNRELEASED = "Unreleased"


def release_dates(changelog: Path = CHANGELOG_PATH) -> dict[Version, date]:
    """The date of each released version, from the CHANGELOG headings."""
    dates = {}
    for line in changelog.read_text().splitlines():
        match = _HEADING.match(line)
        if match and match.group(2) != UNRELEASED:
            dates[parse_version(match.group(1))] = date.fromisoformat(match.group(2))
    return dates


def add_months(day: date, months: int) -> date:
    """`day` plus `months`, on the last day of the month when it has no such day."""
    month_index = day.month - 1 + months
    year, month = day.year + month_index // 12, month_index % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def changelog_head(changelog: Path = CHANGELOG_PATH) -> tuple[Version, bool]:
    """The version of the top CHANGELOG heading, and whether it is released.

    Raises ValueError when the file has no `## [X.Y.Z] - ...` heading.
    """
    for line in changelog.read_text().splitlines():
        match = _HEADING.match(line)
        if match:
            return parse_version(match.group(1)), match.group(2) != UNRELEASED
    raise ValueError(f"{changelog} has no '## [X.Y.Z] - date' heading")


def schema_url(name: str, version: Version | None) -> str:
    """The published URL of one contract schema. None is the `next/` copy."""
    if version is None:
        return f"{SITE_URL}/{NEXT}/schemas/{name}"
    return f"{SITE_URL}/schemas/v{format_version(version)}/{name}"


def schema_path(name: str, version: Version | None, *, root: Path = Path(".")) -> Path:
    """Where the repository keeps one contract schema. None is the working copy."""
    if version is None:
        return root / SCHEMA_DIR / name
    return root / RELEASED_DIR / f"v{format_version(version)}" / name


def load_schema(name: str, version: Version | None, *, root: Path = Path(".")) -> dict[str, Any]:
    with open(schema_path(name, version, root=root)) as f:
        schema: dict[str, Any] = json.load(f)
    return schema


def active_lines(
    today: date,
    released_dir: Path = RELEASED_DIR,
    changelog: Path = CHANGELOG_PATH,
) -> dict[Line, Version]:
    """The release lines the build writes today, with their latest version.

    The newest line is always active. An older line stays active until
    DEPRECATION_MONTHS after the first release of a later line. A line whose
    successor has no date in the CHANGELOG stays active.
    """
    latest = latest_releases(released_dir)
    dates = release_dates(changelog)
    active = {}
    for line, version in latest.items():
        successors = sorted(v for v in dates if line_of(v) > line)
        if successors and today > add_months(dates[successors[0]], DEPRECATION_MONTHS):
            continue
        active[line] = version
    return active


def child_links(doc: Mapping[str, Any]) -> Iterator[dict[str, Any]]:
    """The child links of a registry document, one for each catalog."""
    for link in doc.get("links", []):
        if link.get("rel") == "child":
            yield link


def coverage_href(base_url: str, catalog_id: str) -> str:
    return f"{base_url}coverage/{catalog_id}.json"


def publish(export: Mapping[str, Any], *, base_url: str, extension_url: str) -> dict[str, Any]:
    """The published registry document, built from the git store.

    `base_url` is the published directory with a trailing slash, for example
    https://registry.portolan-sdi.org/v0.1/. The result has the field set of
    the working schema. Use `project` to cut it to a released field set.
    """
    doc = copy.deepcopy(dict(export))
    for old, new in ROOT_RENAMES.items():
        if old in doc:
            doc[new] = doc.pop(old)
    doc["stac_extensions"] = [extension_url]
    self_href = f"{base_url}catalogs.json"
    for link in doc.get("links", []):
        if link.get("rel") in ("root", "self"):
            link["href"] = self_href
    for link in child_links(doc):
        licenses = link.get(PREFIX + "licenses") or {}
        link[PREFIX + "licenses"] = [
            {"id": license_id, "count": count} for license_id, count in sorted(licenses.items())
        ]
        link[PREFIX + "coverage"] = coverage_href(base_url, link[PREFIX + "id"])
    return doc


def declared_fields(extension: Mapping[str, Any]) -> tuple[set[str], set[str]]:
    """The registry fields that an extension schema declares.

    Returns the root fields and the child-link fields.
    """
    definitions = extension["definitions"]
    root = set(definitions["root"]["properties"])
    link = {name for name in definitions["link"]["then"]["properties"] if name.startswith(PREFIX)}
    return root, link


def project(doc: Mapping[str, Any], extension: Mapping[str, Any]) -> dict[str, Any]:
    """Drop each registry field that the extension schema does not declare.

    An additive release adds fields. `next/` gains a field when it lands on
    main, but a released tree such as `v0.1/` keeps the shape of its latest
    release until the next release in its line.
    Fields outside the registry prefix are STAC fields and stay.
    """
    root_fields, link_fields = declared_fields(extension)
    projected = {k: v for k, v in doc.items() if not k.startswith(PREFIX) or k in root_fields}
    projected["links"] = [
        (
            {k: v for k, v in link.items() if not k.startswith(PREFIX) or k in link_fields}
            if link.get("rel") == "child"
            else dict(link)
        )
        for link in doc.get("links", [])
    ]
    return projected


def split_coverage(
    coverage: Mapping[str, Any], *, base_url: str, coverage_schema_url: str
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Split the combined store coverage into an index and one file per catalog."""
    generated = coverage.get("generated")
    files: dict[str, dict[str, Any]] = {}
    entries = []
    for record in coverage.get("catalogs", []):
        catalog_id = record["id"]
        files[catalog_id] = {
            "$schema": coverage_schema_url,
            "generated": generated,
            "id": catalog_id,
            "collection_count": record.get("collection_count", 0),
            "collections": record.get("collections", []),
        }
        entries.append(
            {
                "id": catalog_id,
                "collection_count": record.get("collection_count", 0),
                "href": coverage_href(base_url, catalog_id),
            }
        )
    index = {"$schema": coverage_schema_url, "generated": generated, "catalogs": entries}
    return index, files
