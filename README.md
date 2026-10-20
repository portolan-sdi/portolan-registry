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

You can request a catalog's removal or deprecation even if someone else registered it. A removal takes the catalog out of the registry, while a deprecation keeps it listed with a flag. The flag can point to the catalog that replaces it.

1. Open a [catalog removal or deprecation request](https://github.com/portolan-sdi/portolan-registry/issues/new?template=catalog-removal.yml). Include the catalog ID, the action, your relationship to the catalog, and your reason.
2. Open a pull request that links to your request and changes `catalogs/<id>.yaml`:
   - To remove the catalog, delete the file.
   - To deprecate it, add `deprecated: true`. You can also add the ID of a successor and a reason:

     ```yaml
     url: https://example.org/catalog.json
     submitter_email: you@example.org
     deprecated: true
     superseded_by: example-replacement
     deprecated_reason: The publisher now registers its own catalog.
     ```

The registry emails your request to the person who registered the catalog. If someone else registered it, a maintainer waits 14 days for that person to object before approving the change. Requests for spam or legal reasons do not require this waiting period. The maintainer reviews your request and may ask you to confirm your relationship to the publisher. If the maintainer accepts your request, they approve and merge the pull request.

Put the reason in quotes if it contains `#` or `: `. YAML reads ` #` as the start of a comment and drops the rest of the line.

The `superseded_by` field must reference a registered catalog, or one that the same pull request registers. A new entry cannot be deprecated, and one pull request cannot both deprecate an entry and change its URL. You cannot delete an entry while another entry lists it as its successor.

After a removal, the next registry update drops the catalog from [`exports/catalogs.json`](./exports/catalogs.json) and [`exports/coverage-bboxes.json`](./exports/coverage-bboxes.json). The export's Git history records the removal. If you want to register the same catalog again, use the same ID and URL and get a maintainer's approval. The registry rejects that URL under any other ID. You cannot reuse the ID for another catalog.

After a deprecation, the catalog's link in the export includes `portolan_registry:deprecated: true`, `portolan_registry:deprecated_since`, `portolan_registry:deprecated_reason`, and `portolan_registry:superseded_by`. The registry reads the date from the commit that added `deprecated: true`. To undo a deprecation, open a pull request that deletes the field.

### Catalog status

| Status or flag | Meaning |
|---|---|
| `valid` | The catalog passed its last nightly check. |
| `stale` | The catalog failed one or more nightly checks in a row. The registry emails the person who registered it on the first failure. |
| `removed` | The catalog failed every nightly check for 30 days. Its link stays in the export with this status, and its entry file stays in `catalogs/`. |
| `deprecated` | A flag, separate from the status. A deprecated catalog fails a check when it does not answer, or when `rashid` or `stac-node-validator` rejects it. |

A catalog that is not deprecated fails a check only when it does not answer. The 30 days start at the first failure, and one passing night resets them. A removed catalog returns to `valid` when an update to its entry file triggers a registry update and the catalog passes.

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
