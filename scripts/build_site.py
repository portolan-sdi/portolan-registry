#!/usr/bin/env python3
"""Build the registry.portolan-sdi.org site from the git store.

Run from the repository root:

    uv run --frozen --group site scripts/build_site.py --out _site

The site holds every released schema, the `next/` tree in the shape of the
working schemas, and one `vN/` tree for each supported major version. The
script validates every file it writes and exits 1 when a check fails, so the
Pages workflow never deploys a tree that breaks its own contract.
"""

from __future__ import annotations

import argparse
import json
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq
from jsonschema import Draft7Validator

from registry.contract import (
    CHANGELOG_PATH,
    COVERAGE,
    EXTENSION,
    NEXT,
    RELEASED_DIR,
    SCHEMA_FILES,
    SITE_URL,
    Version,
    active_majors,
    child_links,
    declared_fields,
    format_version,
    load_schema,
    project,
    publish,
    released_versions,
    schema_path,
    schema_url,
    split_coverage,
)
from registry.coverage import COVERAGE_PATH
from registry.export import EXPORT_PATH
from registry.geoparquet import FIELD_TYPES, build_table, write_table
from registry.report import log
from registry.validators import ValidatorError, run_stac_node_validator

DOMAIN = "registry.portolan-sdi.org"

# GitHub warns at 50 MB for one file. Pages has no smaller limit, but a file
# this size also means the per-catalog split no longer keeps files small.
MAX_FILE_BYTES = 50 * 1024 * 1024

INDEX_HTML = """<!doctype html>
<html lang="en">
<meta charset="utf-8">
<title>Portolan registry</title>
<h1>Portolan registry</h1>
<p>The registry of Portolan catalogs, as a versioned contract.</p>
<ul>
{majors}<li><a href="next/catalogs.json">next/catalogs.json</a> (unreleased, no stability promise)</li>
</ul>
<p>Read the contract rules in the
<a href="https://github.com/portolan-sdi/portolan-registry#registry-contract">registry README</a>.</p>
</html>
"""


class BuildError(Exception):
    """Raised when a built file breaks its contract."""


@dataclass(frozen=True)
class Tree:
    """One published directory, such as `next/` or `v1/`."""

    name: str
    version: Version | None

    @property
    def base_url(self) -> str:
        return f"{SITE_URL}/{self.name}/"


def write_json(path: Path, doc: Mapping[str, Any]) -> None:
    """Write compact JSON. Pages compresses it, and nobody diffs it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(doc, f, separators=(",", ":"), ensure_ascii=False)


def _errors(validator: Draft7Validator, doc: Any, label: str) -> list[str]:
    """One line per distinct message, with the places it occurs.

    A field that the store stops writing fails on every child link. One line
    with a count reads better than 86 lines that say the same thing.
    """
    places: dict[str, list[str]] = {}
    for error in validator.iter_errors(doc):
        path = "/".join(str(p) for p in error.absolute_path) or "(root)"
        places.setdefault(error.message, []).append(path)
    lines = []
    for message, paths in places.items():
        where = paths[0] if len(paths) == 1 else f"{paths[0]} and {len(paths) - 1} more"
        lines.append(f"{label}: {where}: {message}")
    return lines


def _validator(schema: Mapping[str, Any]) -> Draft7Validator:
    # Without the format checker, `date-time` and `uri` only annotate.
    return Draft7Validator(schema, format_checker=Draft7Validator.FORMAT_CHECKER)


def build_tree(
    tree: Tree,
    export: Mapping[str, Any],
    coverage: Mapping[str, Any],
    *,
    out: Path,
    root: Path,
) -> Path:
    """Write one tree and check it against its own schemas.

    Returns the path of its catalogs.json.
    """
    extension = load_schema(EXTENSION, tree.version, root=root)
    coverage_schema = load_schema(COVERAGE, tree.version, root=root)
    directory = out / tree.name

    doc = publish(
        export,
        base_url=tree.base_url,
        extension_url=schema_url(EXTENSION, tree.version),
    )
    doc = project(doc, extension)
    catalogs_path = directory / "catalogs.json"
    write_json(catalogs_path, doc)

    index, files = split_coverage(
        coverage,
        base_url=tree.base_url,
        coverage_schema_url=schema_url(COVERAGE, tree.version),
    )
    write_json(directory / "coverage" / "index.json", index)
    for catalog_id, record in files.items():
        write_json(directory / "coverage" / f"{catalog_id}.json", record)

    _, link_fields = declared_fields(extension)
    untyped = link_fields - FIELD_TYPES.keys()
    if untyped:
        raise BuildError(
            f"{tree.name}: no parquet type in registry/geoparquet.py for {', '.join(sorted(untyped))}"
        )
    table = build_table(doc, link_fields)
    parquet_path = directory / "catalogs.parquet"
    write_table(table, parquet_path)

    errors = _errors(_validator(extension), doc, f"{tree.name}/catalogs.json")
    coverage_validator = _validator(coverage_schema)
    errors += _errors(coverage_validator, index, f"{tree.name}/coverage/index.json")
    for catalog_id, record in files.items():
        errors += _errors(coverage_validator, record, f"{tree.name}/coverage/{catalog_id}.json")

    children = list(child_links(doc))
    missing = {link["portolan_registry:id"] for link in children} - files.keys()
    if missing:
        errors.append(f"{tree.name}: no coverage file for {', '.join(sorted(missing))}")
    rows = pq.read_metadata(parquet_path).num_rows
    if rows != len(children):
        errors.append(f"{tree.name}/catalogs.parquet: {rows} rows for {len(children)} child links")
    if errors:
        raise BuildError("\n".join(errors))
    return catalogs_path


def copy_schemas(out: Path, *, root: Path) -> None:
    """Publish every released schema and the working copies under `next/`."""
    for version in released_versions(root / RELEASED_DIR):
        for name in SCHEMA_FILES:
            target = out / "schemas" / f"v{format_version(version)}" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(schema_path(name, version, root=root), target)
    for name in SCHEMA_FILES:
        target = out / NEXT / "schemas" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(schema_path(name, None, root=root), target)


def _clear(out: Path) -> None:
    """Remove an earlier build. Refuse any other directory that holds files.

    The CNAME file marks a build, so a wrong --out such as `.` stops here
    instead of deleting the checkout.
    """
    if not out.exists():
        return
    cname = out / "CNAME"
    is_build = cname.is_file() and cname.read_text().strip() == DOMAIN
    if any(out.iterdir()) and not is_build:
        raise BuildError(f"{out} holds files and is not an earlier site build")
    shutil.rmtree(out)


def check_sizes(out: Path) -> None:
    large = [
        f"{path.relative_to(out)}: {path.stat().st_size} bytes"
        for path in sorted(out.rglob("*"))
        if path.is_file() and path.stat().st_size > MAX_FILE_BYTES
    ]
    if large:
        raise BuildError("files over 50 MB:\n" + "\n".join(large))


def stac_validate(catalogs: list[tuple[Tree, Path]], *, root: Path) -> None:
    """Run stac-node-validator on each catalogs.json, with the local schemas."""
    findings = []
    for tree, path in catalogs:
        local = {
            schema_url(EXTENSION, tree.version): schema_path(EXTENSION, tree.version, root=root)
        }
        for finding in run_stac_node_validator(path, schema_map=local):
            findings.append(f"{tree.name}/catalogs.json: {finding.rule_id}: {finding.message}")
    if findings:
        raise BuildError("\n".join(findings))


def build_site(
    *,
    out: Path,
    root: Path = Path("."),
    today: date | None = None,
    stac_validator: bool = True,
) -> list[Tree]:
    """Build the whole site into `out`. Returns the trees it wrote."""
    today = today or datetime.now(UTC).date()
    with open(root / EXPORT_PATH) as f:
        export = json.load(f)
    with open(root / COVERAGE_PATH) as f:
        coverage = json.load(f)

    _clear(out)
    out.mkdir(parents=True)
    (out / "CNAME").write_text(DOMAIN + "\n")
    copy_schemas(out, root=root)

    majors = active_majors(today, root / RELEASED_DIR, root / CHANGELOG_PATH)
    trees = [
        Tree(NEXT, None),
        *(Tree(f"v{major}", version) for major, version in sorted(majors.items())),
    ]
    catalogs = []
    failures = []
    # Build every tree before failing, so one run reports both a `next/`
    # problem and the `vN/` problem it causes.
    for tree in trees:
        try:
            catalogs.append((tree, build_tree(tree, export, coverage, out=out, root=root)))
        except BuildError as e:
            failures.append(str(e))
    if failures:
        raise BuildError("\n".join(failures))
    links = "".join(
        f'<li><a href="{tree.name}/catalogs.json">{tree.name}/catalogs.json</a> '
        f"(contract {format_version(tree.version)})</li>\n"
        for tree in trees
        if tree.version is not None
    )
    (out / "index.html").write_text(INDEX_HTML.format(majors=links))

    check_sizes(out)
    if stac_validator:
        stac_validate(catalogs, root=root)
    return trees


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="_site", help="Directory to write the site to.")
    parser.add_argument(
        "--no-stac-validator",
        action="store_true",
        help="Skip stac-node-validator, for a machine without Node.js.",
    )
    args = parser.parse_args(argv)
    try:
        trees = build_site(out=Path(args.out), stac_validator=not args.no_stac_validator)
    except (BuildError, ValidatorError) as e:
        log(f"\n=== REFUSING TO PUBLISH THE SITE ===\n{e}")
        return 1
    log(f"Built {args.out}: {', '.join(tree.name for tree in trees)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
