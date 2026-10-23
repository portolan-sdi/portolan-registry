# Portolan registry

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

The Portolan registry brings independently hosted [Portolan](https://github.com/portolan-sdi/portolan-spec) catalogs into one searchable network. Catalog owners keep their data in their own storage. Submit your Portolan catalog to help other users find and search it.

**[Browse registered catalogs](https://www.portolan-sdi.org/registry)**, use the [registry QGIS plugin](https://plugins.qgis.org/plugins/portolan_registry_qgis/), or [download catalog metadata](https://registry.portolan-sdi.org/) for your own tools.

## Quickstart

1. Publish a publicly accessible catalog that follows the [Portolan specification](https://github.com/portolan-sdi/portolan-spec).
2. Run [rashid](https://github.com/portolan-sdi/rashid) and [stac-node-validator](https://github.com/m-mohr/stac-node-validator), then review your catalog with the [catalog grader skill](https://github.com/portolan-sdi/portolan-skills/blob/main/skills/catalog-grader/SKILL.md). Fix the validation errors and use the grade to improve your catalog before submission.
3. Open the [registry page](https://www.portolan-sdi.org/registry) and submit your root `catalog.json` URL and an email address.

You can also submit through GitHub. Fork this repository, add `catalogs/your-catalog.yaml`, and open a pull request:

```yaml
url: https://example.org/catalog.json
submitter_email: you@example.org
```

Use the address of the `catalog.json` file itself. The registry rejects a URL that contains `#`, such as a STAC Browser link, because a server never receives the part after `#`.

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

## Consuming catalog metadata

You can access the registry directly from your own tools using the JSON files published at [registry.portolan-sdi.org](https://registry.portolan-sdi.org/).

### Available files

- **`catalogs.json`** — A [STAC](https://stacspec.org/en/) catalog containing links to registered catalogs, with additional metadata in the `portolan_registry:*` fields.
- **`coverage/index.json`** — An index of geographic coverage files for each catalog. Each file contains the bounding boxes of that catalog's collections.

### Schema compatibility

The registry uses versioned schemas to provide a predictable format for downstream tools. Versions apply to the **structure of the published metadata**, not the catalogs themselves. Catalogs can be added, updated, or removed without changing the schema version.

Each released version belongs to a compatibility line:

- **Compatible changes** can add new fields, but existing fields retain their names, types, and meanings. Tools should ignore fields they don't recognize.
- **Breaking changes** require a new compatibility line. When a new line is released, the previous line's data URLs are retired.

For production integrations, use a released version listed at [registry.portolan-sdi.org](https://registry.portolan-sdi.org/). The `next/` directory contains unreleased changes and provides no compatibility guarantees.

Each published JSON file identifies its schema. See the [schema guide](./schema/README.md) for schema definitions, compatibility rules, and release instructions.

The older [`exports/catalogs.json`](./exports/catalogs.json) remains available in this repository, but its schema is not versioned.

## License

This repository uses the [Apache License 2.0](./LICENSE). Registered datasets retain their own licenses; check each catalog before use.
