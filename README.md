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

You can request removal or deprecation even if someone else registered the catalog:

- **Remove** the catalog to take it out of the registry.
- **Deprecate** the catalog to keep it listed as deprecated. You can also point users to a replacement catalog.

1. Open a [catalog removal or deprecation request](https://github.com/portolan-sdi/portolan-registry/issues/new?template=catalog-removal.yml). Include the catalog ID, your chosen action, your relationship to the catalog, and your reason.
2. Open a pull request and link to your request. To remove the catalog, delete `catalogs/<id>.yaml`. To deprecate it, add `deprecated: true` to that file. The replacement ID and reason are optional:

   ```yaml
   url: https://example.org/catalog.json
   submitter_email: you@example.org
   deprecated: true
   superseded_by: example-replacement
   deprecated_reason: The publisher has registered a replacement catalog.
   ```

The registry emails your request to the person who registered the catalog. If someone else registered it, a maintainer allows 14 days for that person to object before approving the change. Requests involving spam or legal concerns do not require this waiting period. A maintainer reviews each request and may ask you to confirm your relationship to the publisher before merging the pull request.

If your reason contains `#` or a colon followed by a space, put it in quotes so YAML reads the full value as text.

If you set `superseded_by`, use the ID of a catalog that is already registered or that you register in the same pull request. Register a catalog before deprecating it, and submit URL changes in a separate pull request. Before deleting a catalog, update any entries that name it in `superseded_by`.

After a maintainer merges a removal, the next registry update removes the catalog from the [JSON registry](./exports/catalogs.json) and [coverage export](./exports/coverage-bboxes.json). Git history preserves the record. To register the same catalog again, use its original ID and URL and get a maintainer's approval. You cannot register a different catalog with that ID or URL.

For deprecated catalogs, the export includes the flag, date, reason, and replacement ID defined in the [export schema](./schema/export.schema.json). The date comes from the commit that added `deprecated: true`. To reverse a deprecation, open a pull request that removes `deprecated`, `superseded_by`, and `deprecated_reason` from the entry.

### Catalog status

| Status | Meaning |
|---|---|
| `valid` | The catalog passed its most recent completed check. |
| `stale` | The catalog failed a nightly check and has not passed since. The registry emails the person who registered it on the first failure. |
| `removed` | The catalog failed a check at least 30 days after its first failure and has not passed since. Its export link and entry file remain. |

The `deprecated` flag applies independently of these statuses. A deprecated catalog fails a check if the registry cannot fetch it or if `rashid` or `stac-node-validator` reports an error. Other catalogs fail only when the registry cannot fetch them. If a deprecated catalog is reachable but the validators return no result, the registry preserves its status and failure date.

A passing check resets the 30-day period. To restore a catalog with status `removed`, update its entry file to trigger a registry update. If the catalog passes, its status returns to `valid`.

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
