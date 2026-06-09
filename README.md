# Portolan Registry

A discovery layer for Portolan catalogs - STAC-based geospatial data catalogs with versioning and reproducibility.

## What is Portolan Registry?

Portolan Registry is a community-maintained index of [Portolan](https://github.com/portolan-sdi/portolan-spec)-compatible STAC catalogs. It provides a central place to discover, browse, and validate geospatial data catalogs that follow the Portolan specification.

## Architecture

The registry uses a Git-based architecture with no database:

- **Source of truth**: YAML files in `catalogs/` directory
- **Schema validation**: JSON Schema in `schema/entry.schema.json`
- **Exports**: Machine-readable `exports/catalogs.json` generated from YAML sources
- **CI validation**: Automated checks for schema compliance and URL liveness

This design ensures transparency, versioning, and easy contribution through standard Git workflows.

## Browse Catalogs

**Web UI**: Coming soon at [portolan.dev/registry](https://portolan.dev/registry)

**Direct access**: Browse YAML files in the [`catalogs/`](./catalogs) directory

**Machine-readable**: After catalog entries are merged, they appear in `exports/catalogs.json`

## Submit a Catalog

See [CONTRIBUTING.md](./CONTRIBUTING.md) for step-by-step instructions on submitting your catalog.

**Requirements**: Your catalog must be a valid Portolan catalog with:
- Valid STAC catalog structure
- `versions.json` for version tracking
- `.portolan/` directory with Portolan metadata

## Schema Reference

Each catalog entry includes:

| Field | Type | Description |
|-------|------|-------------|
| `url` | string | URL to `catalog.json` root |
| `title` | string | Human-readable catalog name (3-100 chars) |
| `description` | string | Brief description (10-500 chars) |
| `maintainer` | string | Person or organization name |
| `contact` | string | Email address |
| `access` | enum | `public` or `authenticated` |
| `tags` | array | Lowercase kebab-case tags for discovery |
| `submitted` | date | ISO 8601 date (YYYY-MM-DD) |
| `validation` | object | Validation status and results |

Full schema: [`schema/entry.schema.json`](./schema/entry.schema.json)

## Related Projects

- [portolan-cli](https://github.com/portolan-sdi/portolan-cli) - Command-line tool for creating and managing Portolan catalogs
- [portolan-spec](https://github.com/portolan-sdi/portolan-spec) - Portolan specification and format documentation

## License

This project is licensed under the Apache License 2.0. See [LICENSE](./LICENSE) for details.
