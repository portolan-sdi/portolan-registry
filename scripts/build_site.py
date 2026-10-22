#!/usr/bin/env python3
"""Build the registry.portolan-sdi.org site from the git store.

Run from the repository root:

    uv run --frozen --group site scripts/build_site.py --out _site

The site holds every released schema, the `next/` tree in the shape of the
working schemas, and the tree of the newest release line, such as `v0.1/` or
`v1/`. The script validates every file it writes and exits 1 when a check
fails, so the Pages workflow never deploys a tree that breaks its own
contract.
"""

from __future__ import annotations

import argparse
import html
import json
import shutil
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq
from jsonschema import Draft7Validator

from registry.contract import (
    COVERAGE,
    EXTENSION,
    NEXT,
    RELEASED_DIR,
    SCHEMA_FILES,
    SITE_URL,
    Version,
    child_links,
    declared_fields,
    format_version,
    line_name,
    line_of,
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

# The static files of the landing page. portolan-ops syncs the brand files
# into this directory. style.css belongs to this repo.
ASSETS_DIR = Path("site")

FONTS_URL = (
    "https://fonts.googleapis.com/css2?family=Hanken+Grotesk:wght@400;600;700"
    "&family=JetBrains+Mono:wght@400;500&display=swap"
)

INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Portolan registry</title>
<meta name="description" content="Download Portolan catalog metadata as JSON or GeoParquet, with collection bounding boxes.">
<link rel="icon" href="portolan-logomark-4163cc.svg" type="image/svg+xml">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="{fonts_url}">
<link rel="stylesheet" href="_brand-vars.css">
<link rel="stylesheet" href="style.css">
</head>
<body>
<div class="page">
<header>
<a href="https://www.portolan-sdi.org/"><img src="portolan-logo-horizontal-light.svg" alt="Portolan home" width="143" height="32"></a>
</header>
<main>
<p class="kicker">registry.portolan-sdi.org</p>
<h1>Portolan registry</h1>
<p class="lede">Download metadata for registered Portolan catalogs as JSON or GeoParquet.
Each catalog's coverage file lists the bounding boxes of its collections.
To explore the registry, <a href="https://www.portolan-sdi.org/registry">browse and search registered catalogs</a>.</p>
<dl class="stats">
<div><dt>Catalogs</dt><dd>{count}</dd></div>
<div><dt>Last updated</dt><dd><time datetime="{generated}">{updated}</time></dd></div>
</dl>
<h2>Registry data</h2>
<div class="table-wrap">
<table>
<thead><tr><th>Path</th><th>Schema version</th><th>Files</th></tr></thead>
<tbody>
{trees}</tbody>
</table>
</div>
{release_note}<h2>Schemas</h2>
<div class="table-wrap">
<table>
<thead><tr><th>Version</th><th>Files</th></tr></thead>
<tbody>
{schemas}</tbody>
</table>
</div>
<p class="note">Released schemas never change.
Versions follow <a href="https://semver.org/spec/v2.0.0.html">Semantic Versioning</a>.
For compatibility and versioning rules, see the
<a href="https://github.com/portolan-sdi/portolan-registry/tree/main/schema#versions">schema guide</a>.
For release history, see the
<a href="https://github.com/portolan-sdi/portolan-registry/blob/main/schema/CHANGELOG.md">contract changelog</a>.</p>
</main>
<footer>
<p>For file formats and compatibility rules, see the
<a href="https://github.com/portolan-sdi/portolan-registry#registry-contract">registry documentation</a>.
<a href="https://github.com/portolan-sdi/portolan-registry">View source code on GitHub</a>.</p>
</footer>
</div>
</body>
</html>
"""

TREE_FILES = ("catalogs.json", "catalogs.parquet", "coverage/index.json")

NO_RELEASE_NOTE = (
    '<p class="note">The registry has not published a release yet. '
    "Files under <code>next/</code> include changes from <code>main</code> "
    "and can change without notice.</p>\n"
)


class BuildError(Exception):
    """Raised when a built file breaks its contract."""


@dataclass(frozen=True)
class Tree:
    """One published directory, such as `next/` or `v0.1/`."""

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
    try:
        table = build_table(doc, link_fields)
    except ValueError as e:
        raise BuildError(f"{tree.name}/catalogs.parquet: {e}") from e
    parquet_path = directory / "catalogs.parquet"
    write_table(table, parquet_path)

    errors = _errors(_validator(extension), doc, f"{tree.name}/catalogs.json")
    coverage_validator = _validator(coverage_schema)
    errors += _errors(coverage_validator, index, f"{tree.name}/coverage/index.json")
    for catalog_id, record in files.items():
        errors += _errors(coverage_validator, record, f"{tree.name}/coverage/{catalog_id}.json")

    children = list(child_links(doc))
    ids = {link["portolan_registry:id"] for link in children}
    missing = ids - files.keys()
    if missing:
        errors.append(f"{tree.name}: no coverage file for {', '.join(sorted(missing))}")
    orphans = files.keys() - ids
    if orphans:
        errors.append(f"{tree.name}: no child link for coverage {', '.join(sorted(orphans))}")
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


def copy_assets(out: Path, *, root: Path) -> None:
    """Copy the landing page's static assets to the site root.

    Refuse a name that the build writes itself, such as CNAME, so an asset
    never replaces a generated file. Refuse a directory, so no asset is
    left out without notice.
    """
    for path in sorted((root / ASSETS_DIR).iterdir()):
        target = out / path.name
        if not path.is_file():
            raise BuildError(f"{path}: the site assets must be files")
        if path.name == "index.html" or target.exists():
            raise BuildError(f"{path}: the build writes {path.name} itself")
        shutil.copyfile(path, target)


def _file_list(base: str, names: Iterable[str]) -> str:
    items = "".join(f'<li><a href="{base}{name}"><code>{name}</code></a></li>' for name in names)
    return f"<ul>{items}</ul>"


def render_index(trees: list[Tree], releases: list[Version], export: Mapping[str, Any]) -> str:
    """Render the landing page with data downloads and schema links."""
    rows = []
    # The released line first, because a tool pins it. `next/` is last.
    for tree in sorted(trees, key=lambda t: t.version is None):
        contract = "Unreleased" if tree.version is None else format_version(tree.version)
        rows.append(
            f"<tr><td><code>{tree.name}/</code></td><td>{contract}</td>"
            f"<td>{_file_list(f'{tree.name}/', TREE_FILES)}</td></tr>\n"
        )
    schemas = [
        f"<tr><td>{format_version(version)}</td>"
        f"<td>{_file_list(f'schemas/v{format_version(version)}/', SCHEMA_FILES)}</td></tr>\n"
        for version in reversed(releases)
    ]
    schemas.append(
        f"<tr><td>Unreleased</td><td>{_file_list(f'{NEXT}/schemas/', SCHEMA_FILES)}</td></tr>\n"
    )
    # HTML allows at most 3 digits of fractional seconds in `datetime`.
    generated = datetime.fromisoformat(str(export["generated"])).isoformat(timespec="seconds")
    return INDEX_HTML.format(
        fonts_url=html.escape(FONTS_URL),
        count=int(export["count"]),
        generated=html.escape(generated),
        updated=html.escape(generated[:10]),
        trees="".join(rows),
        release_note="" if releases else NO_RELEASE_NOTE,
        schemas="".join(schemas),
    )


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
    stac_validator: bool = True,
) -> list[Tree]:
    """Build the whole site into `out`. Returns the trees it wrote."""
    with open(root / EXPORT_PATH) as f:
        export = json.load(f)
    with open(root / COVERAGE_PATH) as f:
        coverage = json.load(f)

    _clear(out)
    out.mkdir(parents=True)
    (out / "CNAME").write_text(DOMAIN + "\n")
    copy_schemas(out, root=root)
    copy_assets(out, root=root)

    # Only the newest line has a tree. A breaking release stops the tree of
    # the previous line, and its schemas stay under schemas/.
    trees = [Tree(NEXT, None)]
    releases = released_versions(root / RELEASED_DIR)
    if releases:
        trees.append(Tree(line_name(line_of(releases[-1])), releases[-1]))
    catalogs = []
    failures = []
    # Build every tree before failing, so one run reports both a `next/`
    # problem and the problem it causes in a released tree.
    for tree in trees:
        try:
            catalogs.append((tree, build_tree(tree, export, coverage, out=out, root=root)))
        except BuildError as e:
            failures.append(str(e))
    if failures:
        raise BuildError("\n".join(failures))
    (out / "index.html").write_text(render_index(trees, releases, export))

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
