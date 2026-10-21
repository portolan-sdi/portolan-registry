# Portolan registry

The Portolan registry brings independently hosted [Portolan](https://github.com/portolan-sdi/portolan-spec) catalogs into one searchable network. Catalog owners keep their data in their own storage. Submit your Portolan catalog to help other users find and search it.

**[Browse registered catalogs](https://www.portolan-sdi.org/registry)**, explore the [registry QGIS plugin](https://plugins.qgis.org/plugins/portolan_registry_qgis/), or read the [JSON registry](./exports/catalogs.json) from your own tools.

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

## Schemas

The [entry schema](./schema/entry.schema.json) defines submissions. The [export schema](./schema/export.schema.json) defines the JSON registry that tools consume. The [Portolan specification](https://github.com/portolan-sdi/portolan-spec) sets the requirements for catalogs.

## Roadmap

We plan to:

- Introduce versions for the registry schemas.
- Improve the catalog grader integration and its feedback.
- Add [STAC GeoParquet](https://github.com/stac-utils/stac-geoparquet) support.

Follow the [Portolan planning board](https://github.com/orgs/portolan-sdi/projects/1) for project plans.

## License

This repository uses the [Apache License 2.0](./LICENSE). Registered datasets retain their own licenses; check each catalog before use.
