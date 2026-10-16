# Portolan registry

The Portolan registry is a catalog of independently hosted [Portolan](https://github.com/portolan-sdi/portolan-spec) catalogs. Submit yours to make it discoverable alongside data from other publishers, with shared metadata that people and agents can search.

You submit a catalog URL. Your data stay in your storage, where users query them directly. Your catalog keeps working independently of the registry.

The long-term goal is for Portolan to become the default way to publish geospatial data. The registry is a first step: catalogs from cities, researchers, mapping agencies, and other publishers become searchable together while remaining distributed.

**[Browse registered catalogs](https://www.portolan-sdi.org/registry)** or read the [JSON registry](./exports/catalogs.json) from your own tools.

## Quickstart

1. Publish a publicly accessible catalog that follows the [Portolan specification](https://github.com/portolan-sdi/portolan-spec).
2. Run [rashid](https://github.com/portolan-sdi/rashid) and [stac-node-validator](https://github.com/m-mohr/stac-node-validator), then review your catalog with the [catalog grader skill](https://github.com/portolan-sdi/portolan-skills/blob/main/skills/catalog-grader/SKILL.md). Fix the validation errors and use the grade to improve your catalog before submission.
3. Open the [registry page](https://www.portolan-sdi.org/registry) and submit your root `catalog.json` URL and an email address.

You can also submit through GitHub. Fork this repository, add `catalogs/your-catalog.yaml`, and open a pull request:

```yaml
url: https://example.org/catalog.json
submitter_email: you@example.org
```

Web submissions use the root catalog `id` as the registry ID. Choose a unique filename. The registry also rejects URLs that another entry already uses. It extracts the other metadata from your catalog.

The registry uses your email address for validation failures and catalog feedback. The address is public in [`catalogs/`](./catalogs), but excluded from the JSON registry.

<details>
<summary>Show your catalog's logo</summary>

Add an `icon` link to your root `catalog.json`:

```json
{
  "rel": "icon",
  "href": "./_assets/your-logo.png",
  "type": "image/png",
  "title": "Your organization"
}
```

The `href` can be relative or absolute. Set `type` to a browser-supported image format: PNG, JPEG, GIF, WebP, AVIF, APNG, or SVG. The registry uses `title` as alt text and omits the logo if it cannot fetch the image.

</details>

## What happens when you submit a catalog

Your submission creates a pull request in this repository for validation and a catalog grade.

- **Deterministic validation.** `rashid` checks Portolan requirements, and `stac-node-validator` checks STAC core and extension schemas. These checks run against a copy of your catalog metadata; they do not download and validate every data asset.
- **Catalog grade.** A Codex agent runs the catalog grader skill and posts a comment with its grade, findings, and suggested fixes. This integration is new and under active development.

New catalogs must pass deterministic validation before they enter the registry. The agent's grade provides feedback. If validation fails, fix the reported errors and update your submission.

After your submission passes validation and merges, the registry publishes its metadata in [`exports/catalogs.json`](./exports/catalogs.json). Your catalog then appears on the registry page. Scheduled checks revisit registered catalogs and notify submitters about failures.

## Report a bug

For registry problems, open a [bug report](https://github.com/portolan-sdi/portolan-registry/issues/new?template=bug_report.yml). Include the catalog URL, what you expected, and the steps and output that show the failure.

For problems with a registered catalog's data or documentation, use [catalog feedback](https://github.com/portolan-sdi/portolan-registry/issues/new?template=catalog-feedback.yml). Include its ID, the filename in `catalogs/` without `.yaml`. This sends an email to the catalog's submitter.

## Schemas

The [entry schema](./schema/entry.schema.json) defines submissions. The [export schema](./schema/export.schema.json) defines the JSON registry that tools consume. The [Portolan specification](https://github.com/portolan-sdi/portolan-spec) sets the requirements for catalogs.

Registry schema versioning is planned, along with eventual [STAC GeoParquet](https://github.com/stac-utils/stac-geoparquet) support.

## Roadmap

- Introduce versions for the registry schemas.
- Improve the catalog grader integration and its feedback.
- Add STAC GeoParquet support.

Follow the [Portolan planning board](https://github.com/orgs/portolan-sdi/projects/1) for project plans.

## License

This repository uses the [Apache License 2.0](./LICENSE). Registered datasets retain their own licenses; check each catalog before use.
