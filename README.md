# Portolan registry

The Portolan registry brings independently hosted [Portolan](https://github.com/portolan-sdi/portolan-spec) catalogs into one searchable network. Catalog owners keep their data in their own storage. Submit your Portolan catalog to help other users find and search it.

**[Browse registered catalogs](https://www.portolan-sdi.org/registry)**, explore the [registry QGIS plugin](https://plugins.qgis.org/plugins/portolan_registry_qgis/), or [download catalog metadata](https://registry.portolan-sdi.org/) for your own tools.

## Quickstart

1. Publish a publicly accessible catalog that follows the [Portolan specification](https://github.com/portolan-sdi/portolan-spec).
2. Run [rashid](https://github.com/portolan-sdi/rashid) and [stac-node-validator](https://github.com/m-mohr/stac-node-validator), then review your catalog with the [catalog grader skill](https://github.com/portolan-sdi/portolan-skills/blob/main/skills/catalog-grader/SKILL.md). Fix the validation errors and use the grade to improve your catalog before submission.
3. Open the [registry page](https://www.portolan-sdi.org/registry) and submit your root `catalog.json` URL and an email address.

You can also submit through GitHub. Fork this repository, add `catalogs/your-catalog.yaml`, and open a pull request:

```yaml
url: https://example.org/catalog.json
submitter_email: you@example.org
```

Choose a unique filename. The registry also rejects URLs that another entry already uses. It extracts the remaining metadata from your catalog.

The registry uses your email address to notify you about validation failures and catalog feedback. The [`catalogs/`](./catalogs) directory lists your address publicly, but the JSON registry excludes it.

## What happens when you submit a catalog

Submitting through the registry page creates a pull request in this repository. Each submission receives two checks:

- **Deterministic validation.** `rashid` checks Portolan requirements, and `stac-node-validator` checks STAC core and extension schemas. These checks use a copy of your catalog metadata; they do not download and validate every data asset.
- **Catalog grade.** A Codex agent runs the catalog grader skill and posts a comment with its grade, findings, and suggested fixes. We are still developing this new integration.

New catalogs must pass deterministic validation before they enter the registry. The agent's grade provides feedback. If validation fails, fix the reported errors and update your submission.

After your submission passes validation and a maintainer merges it, the registry publishes its metadata in [`exports/catalogs.json`](./exports/catalogs.json). Your catalog then appears on the registry page. Scheduled checks revisit registered catalogs and notify submitters about failures.

Each night, the registry checks catalog metadata with `rashid` and `stac-node-validator`. If a catalog you registered fails validation, the Monday check includes it in a weekly email with the failed rules and examples. You receive one email for all your catalogs that fail that check. When a catalog passes validation, the emails stop including it. Validation errors leave your catalog listed with status `valid`, unless it is deprecated.

In the [JSON registry](./exports/catalogs.json), `portolan_registry:stac_valid` records the validation result. The `portolan_registry:stac_invalid_since` field records when the registry first detected the current failure. A passing check sets `portolan_registry:stac_valid` to `true` and clears the failure timestamp.

Your catalog's registry ID is its filename without `.yaml`, such as `city-of-example-parcels`. Use lowercase letters and digits, with single hyphens between groups. Once a maintainer merges your submission, you cannot change the ID or reuse it for another catalog. The registry reads the title from your catalog, so the ID does not need to match the title.

## Remove or deprecate a catalog

You can request removal or deprecation even if someone else registered the catalog.

1. Open a [removal or deprecation request](https://github.com/portolan-sdi/portolan-registry/issues/new?template=catalog-removal.yml). Include the catalog ID, your relationship to the catalog, and your reason.
2. Open a pull request that links to your request. Delete `catalogs/<id>.yaml` to remove the listing, or add `deprecated: true` to mark it as deprecated.

Deprecated catalogs stay listed and can point to a replacement. See the [entry schema](./schema/entry.schema.json) for optional fields.

The registry emails the person who registered the catalog, and a maintainer reviews your request. If someone else registered it, they have 14 days to object, except for requests involving spam or legal concerns.

Nightly checks mark unreachable catalogs `stale`, then `removed` after 30 days without recovery. Deprecated catalogs also fail on validation errors. These status changes preserve entry files and export links. Nightly checks skip removed catalogs. To restore one, update its entry file in a pull request. Its status returns to `valid` when it passes validation.

## Report a bug

For registry problems, open a [bug report](https://github.com/portolan-sdi/portolan-registry/issues/new?template=bug_report.yml). Include the catalog URL, what you expected, and the steps and output that show the failure.

For problems with a registered catalog's data or documentation, use [catalog feedback](https://github.com/portolan-sdi/portolan-registry/issues/new?template=catalog-feedback.yml). Include the catalog ID: its filename in `catalogs/` without `.yaml`. The registry emails your feedback to the catalog's submitter.

## Registry contract

The [registry downloads](https://registry.portolan-sdi.org/) include catalog metadata as JSON and GeoParquet, plus collection bounding boxes. The registry contract defines the fields in these files and how they can change between versions.

The current release is 0.1.0. Use `v0.1/` for new integrations. Files under `next/` reflect unreleased changes and can change without notice. The raw [`exports/catalogs.json`](./exports/catalogs.json) on `main` still works for existing integrations, but it has no version and changes with each merge.

Release 0.1.0 uses these paths:

| Path | Contents |
|---|---|
| `v0.1/catalogs.json` | A [SpatioTemporal Asset Catalog (STAC)](https://stacspec.org/en/) with one child link per registered catalog. The `portolan_registry:*` fields describe each catalog. |
| `v0.1/catalogs.parquet` | The same catalog metadata as GeoParquet, with one row per catalog. |
| `v0.1/coverage/index.json` | Links to coverage files, one per catalog. Each file lists collection bounding boxes. |
| `schemas/v0.1.0/` | The extension and coverage schemas for release 0.1.0. The site keeps every released schema unchanged. |

Use a versioned path, such as `v0.1/`, to keep your tool on compatible releases. All 0.1.x releases use this path. Compatible releases may add fields, so your tool should accept fields it doesn't recognize.

To find a file's schema version, read `stac_extensions` in `catalogs.json` or `$schema` in a coverage file.

A breaking release moves the data to a new path, such as `v0.2/`. The previous data path then returns HTTP 404, so you must update your tool to use the new version. The maintainers announce breaking releases in the [Portolan Google Group](https://groups.google.com/g/portolan) and the [Portolan Slack channel](https://cloudnativegeo.slack.com/archives/C0A1JBH9529) before publication.

Before version 1.0.0, breaking changes increase the minor version, such as 0.1.0 to 0.2.0. From 1.0.0 onward, they increase the major version. The contract follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html). The [schema guide](./schema/README.md) lists the rules for each version step and the steps to release a version. The [contract changelog](./schema/CHANGELOG.md) lists each release.

The site rebuilds when changes to the exports, schemas, or site files reach `main`.

## Schemas

Choose a schema based on the file you need to validate:

| Schema | What it validates |
|---|---|
| [Entry schema](./schema/entry.schema.json) | The YAML files in `catalogs/` that you submit to register a catalog. |
| [Extension schema](./schema/extension.json) | Registry fields on the published STAC Catalog and its child links. |
| [Coverage schema](./schema/coverage.json) | The coverage index and each catalog's collection bounding boxes. |
| [Export schema](./schema/export.schema.json) | The internal JSON registry in `exports/catalogs.json`. |

The extension and coverage schemas form the versioned contract. The entry and export schemas check files in this repository and have no version. The [schema guide](./schema/README.md) explains why the export and extension schemas are separate.

These links point to the working schemas. For published JSON files, use the schema URLs they declare. The [Portolan specification](https://github.com/portolan-sdi/portolan-spec) defines the requirements for the catalogs themselves.

## Roadmap

We plan to:

- Improve the catalog grader integration and its feedback.

Follow the [Portolan planning board](https://github.com/orgs/portolan-sdi/projects/1) for project plans.

## License

This repository uses the [Apache License 2.0](./LICENSE). Registered datasets retain their own licenses; check each catalog before use.
