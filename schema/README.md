# Registry schemas

This directory contains four JSON Schemas. The registry contract consists of `extension.json` and `coverage.json`. Tools use these schemas to validate data from [registry.portolan-sdi.org](https://registry.portolan-sdi.org/). The other schemas validate files in this repository. Only the contract schemas have a version.

| Schema | What it validates | Who uses it | Version |
|---|---|---|---|
| [`extension.json`](./extension.json) | The published `catalogs.json`, as an open SpatioTemporal Asset Catalog (STAC) extension. | Tool authors | Contract |
| [`coverage.json`](./coverage.json) | The published coverage files. | Tool authors | Contract |
| [`entry.schema.json`](./entry.schema.json) | The submission files in `catalogs/`. | Submitters and CI | None |
| [`export.schema.json`](./export.schema.json) | The registry data in `exports/catalogs.json`, stored in Git. | CI | None |

Use the contract schemas to validate data in your tools. These schemas accept unknown fields, and a released schema accepts the schema URL of any later release in its line. This way, a tool that validates with an older release of the line keeps working when the registry adds a field. The export schema rejects unknown fields on each child link, so CI can catch a field that the crawler writes by mistake.

The stored data and the published file need separate schemas because they differ in these ways:

- The stored data has `generated` and `count`. The published file has `portolan_registry:generated` and `portolan_registry:count`.
- The stored data uses an object for `portolan_registry:licenses`. The published file uses an array of `{id, count}` objects.
- Only the published file has `stac_extensions` and `portolan_registry:coverage`.
- In the stored data, the `root` and `self` links point to GitHub. In the published file, they point to the site.

A test checks that `extension.json` and `export.schema.json` declare the same link fields, except `portolan_registry:coverage`.

## Versions

The contract follows [Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html). The schemas `extension.json` and `coverage.json` share one version number. The [changelog](./CHANGELOG.md) lists each version.

Your tool reads one release line. A release in a line can add fields or change documentation, but it never removes or changes a field. A breaking change starts a new line with its own path on the site:

- Before 1.0.0, the minor version determines the line. All 0.1.x releases use `v0.1/`. A breaking change increases the version from 0.1.x to 0.2.0.
- From 1.0.0 onward, the major version determines the line. All 1.x.y releases use `v1/`. A breaking change increases the version from 1.x.y to 2.0.0.

Item 4 of the Semantic Versioning specification makes no stability promise before 1.0.0. This registry follows Cargo's rule for 0.x versions, so a 0.x line is also stable.

Use this table to choose how much to increase the version:

| Change | Kind | Before 1.0.0 | From 1.0.0 onward |
|---|---|---|---|
| A change to a description or documentation only | documentation | patch | patch |
| A new optional or nullable field | additive | patch | minor |
| A removed or renamed field | breaking | minor | major |
| A changed type, or a non-null field that becomes nullable | breaking | minor | major |
| A changed unit or meaning | breaking | minor | major |
| A new value in a closed enum such as `portolan_registry:status` | breaking | minor | major |

The site publishes data for the newest release line and `next/`, along with every released schema:

| Path | Contents |
|---|---|
| `vX.Y/` or `vX/` | Data for the newest release line. `catalogs.json` and `catalogs.parquet` have the fields of the line's newest release. |
| `next/` | Data in the format defined by the working schemas on `main`, with no stability promise. |
| `schemas/v0.1.0/` and later | The schemas for each release, with one directory for each version. A released schema never changes. |

Coverage files have the same format in every directory. If a change adds a field to the coverage files, the field appears in the release line's directory before it's released. The coverage schema accepts unknown fields, so this doesn't break a tool.

When a breaking release deploys, the previous line's data path returns HTTP 404. Update any tools that read that path.

To validate a published file, use the schema URL that the file declares. A tool can also use an earlier release from the same line.

## Checks on pull requests

Each pull request must pass these contract checks:

- `Contract Version` runs [`scripts/contract_compat.py`](../scripts/contract_compat.py) to compare the working schemas with the newest release. It fails if the change requires a larger version increase than the version in the top changelog heading allows. It also fails if a breaking change has an `Unreleased` heading or if a released schema changes.
- `Build Site` runs [`scripts/build_site.py`](../scripts/build_site.py) to build each data directory and validate each file against that directory's schemas. It uploads the site as the run's `registry-site` artifact.

The version check can't detect changes to a unit or meaning. For these changes, choose the breaking version yourself.

## Change a schema

1. Edit `extension.json` or `coverage.json`.
2. If you add a child-link field to `extension.json`, also add it to `export.schema.json` and to `FIELD_TYPES` in [`scripts/registry/geoparquet.py`](../scripts/registry/geoparquet.py). Then update the crawler so it writes the field. Otherwise, the tests and the `Build Site` check fail.
3. Use the preceding table to choose the version increase.
4. Check the top heading in [`CHANGELOG.md`](./CHANGELOG.md). Include the version number, because the check skips a bare `## [Unreleased]` heading.
   - If the heading is `## [X.Y.Z] - Unreleased` and the version increase is large enough, keep it.
   - If the version increase is too small, update the version.
   - If the heading has a date, add a new heading before it, such as `## [0.1.1] - Unreleased`.
5. Describe the change under that heading. Use [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) sections, such as `### Added`.
6. Open a pull request. After it merges, `next/` includes the change.

For a breaking change, release the version in the same pull request. The stored data has one format, so merging a breaking change under an `Unreleased` heading would break the current release line's data. Follow the release instructions in the next section.

## Release a version

1. Make sure the top heading in `CHANGELOG.md` is `## [X.Y.Z] - Unreleased` and contains the new version.
2. Run the release script. It copies both schemas to a new directory, such as `schemas/v0.1.0/`, sets their URLs, and replaces `Unreleased` with the date.

   ```console
   uv run --frozen scripts/release_contract.py X.Y.Z
   ```

3. Commit `schemas/vX.Y.Z/` and `schema/CHANGELOG.md`, then open a pull request.
4. If the release is breaking, include the schema change, crawler change, and new export in the same pull request.
5. If the release is breaking, download the `registry-site` artifact from the `Build Site` run so tool authors can test the new line.
6. If the release is breaking, announce it in the [Portolan Google Group](https://groups.google.com/g/portolan) and the [Portolan channel](https://cloudnativegeo.slack.com/archives/C0A1JBH9529) before you merge.
7. Merge the pull request. The `Publish Registry Site` workflow deploys the new data directory. The `Tag Contract Release` workflow tags the commit as `export-vX.Y.Z`.
8. Open `https://registry.portolan-sdi.org/` and check that the page lists the new line.
