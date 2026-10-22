# Registry schemas

This directory holds four JSON Schemas. Two of them are the registry contract, which tools read on [registry.portolan-sdi.org](https://registry.portolan-sdi.org/). The other two check files inside this repository. Only the contract schemas have a version.

| Schema | What it checks | Who reads it | Version |
|---|---|---|---|
| [`extension.json`](./extension.json) | The published `catalogs.json`. It is an open STAC extension. | Tools | Contract |
| [`coverage.json`](./coverage.json) | The published coverage files. | Tools | Contract |
| [`entry.schema.json`](./entry.schema.json) | The submission files in `catalogs/`. | Submitters and CI | None |
| [`export.schema.json`](./export.schema.json) | The git store in `exports/catalogs.json`. | The crawler and CI | None |

Tools validate against the contract schemas. These schemas accept unknown fields, so a tool that uses an older release still works when the registry adds a field. The export schema rejects unknown fields instead, which lets CI catch a field that the crawler writes by mistake. The store and the published file differ in three ways, so they need two schemas:

- The store has `generated` and `count`. The published file has `portolan_registry:generated` and `portolan_registry:count`.
- The store writes `portolan_registry:licenses` as an object. The published file writes an array of `{id, count}` objects.
- Only the published file has `portolan_registry:coverage`.

A test checks that `extension.json` and `export.schema.json` declare the same link fields.

## Versions

The contract uses [Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html). One version number covers `extension.json` and `coverage.json`. The [changelog](./CHANGELOG.md) lists each version.

A tool reads one release line. Releases in one line only add fields. A breaking change starts a new line. The line has its own path on the site:

- Before 1.0.0, the minor version sets the line. `v0.1/` holds every 0.1.x release. A breaking change goes from 0.1.x to 0.2.0.
- From 1.0.0 on, the major version sets the line. `v1/` holds every 1.x.y release. A breaking change goes from 1.x.y to 2.0.0.

Item 4 of the Semantic Versioning specification makes no promise before 1.0.0. This registry uses the rule that Cargo uses for 0.x versions, so a 0.x line is stable too.

Use this table to choose the version step:

| Change | Kind | Before 1.0.0 | From 1.0.0 on |
|---|---|---|---|
| A change to a description or documentation only | documentation | patch | patch |
| A new optional or nullable field | additive | patch | minor |
| A removed or renamed field | breaking | minor | major |
| A changed type, or a non-null field that becomes nullable | breaking | minor | major |
| A changed unit or meaning | breaking | minor | major |
| A new value in a closed enum such as `portolan_registry:status` | breaking | minor | major |

The site publishes the newest line, `next/`, and every released schema:

| Path | Contents |
|---|---|
| `vX.Y/` or `vX/` | The data of the newest line, in the shape of its latest release. |
| `next/` | The data in the shape of the working schemas on `main`. It makes no stability promise. |
| `schemas/v0.1.0/` and later | The schemas of each release, one directory per version. A released schema never changes. |

When a breaking release deploys, the path of the previous line stops. A tool that still reads it gets a 404.

## Checks on pull requests

These required checks guard the contract on each pull request:

- `Contract Version` runs [`scripts/contract_compat.py`](../scripts/contract_compat.py). It compares the working schemas with the latest release. It fails when the change needs a larger step than the top changelog heading names. It also fails on a breaking change under an `Unreleased` heading, and on a change to a released schema.
- `Build Site` runs [`scripts/build_site.py`](../scripts/build_site.py). It builds each tree and validates each file against the schemas of that tree. It uploads the site as the `registry-site` artifact of the run.

The version check can't detect a changed unit or meaning. For that change, name the breaking version yourself.

## Change a schema

1. Edit `extension.json` or `coverage.json`.
2. Find the step in the preceding table.
3. Look at the top heading of [`CHANGELOG.md`](./CHANGELOG.md).
   - If it is `## [X.Y.Z] - Unreleased` and that version is a large enough step, keep it.
   - If it is too small, change the version.
   - If it has a date, add a new heading before it, such as `## [0.1.1] - Unreleased`.
4. Add a line under the heading. Use the sections of [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), such as `### Added`.
5. Open a pull request. After it merges, `next/` serves the change.

A breaking change can't wait under an `Unreleased` heading. The store has one shape, so it would break the data of the current line. Release it in the same pull request. Follow the next section.

## Release a version

1. Make sure the top heading of `CHANGELOG.md` is `## [X.Y.Z] - Unreleased` and names the new version.
2. Run the release script. It copies the two schemas to a new directory, such as `schemas/v0.1.0/`, sets their URLs, and replaces `Unreleased` with the date.

   ```console
   uv run --frozen scripts/release_contract.py X.Y.Z
   ```

3. Commit `schemas/vX.Y.Z/` and `schema/CHANGELOG.md`, then open a pull request.
4. For a breaking release, put the schema change, the crawler change, and the new export in the same pull request.
5. For a breaking release, download the `registry-site` artifact from the `Build Site` run. Tool authors can test the new line with it.
6. For a breaking release, announce it in the [Portolan Google Group](https://groups.google.com/g/portolan) and the [Portolan channel](https://cloudnativegeo.slack.com/archives/C0A1JBH9529) before you merge.
7. Merge the pull request. The `Publish Registry Site` workflow deploys the new tree. The `Tag Contract Release` workflow tags the commit as `export-vX.Y.Z`.
8. Open `https://registry.portolan-sdi.org/` and check that the page lists the new line.
