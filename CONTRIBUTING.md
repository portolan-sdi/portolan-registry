# Contributing to Portolan Registry

Thank you for contributing to the Portolan Registry! This guide explains how to submit your catalog for inclusion.

## Prerequisites

Your catalog must be a valid **Portolan catalog**, which means:

1. **Valid STAC structure** - Conforms to the [STAC specification](https://stacspec.org/)
2. **versions.json** - Includes a `versions.json` file for version tracking
3. **Portolan directory** - Contains a `.portolan/` directory with Portolan-specific metadata

If you need to create a Portolan catalog, use [portolan-cli](https://github.com/radiantearth/portolan-cli):

```bash
portolan init my-catalog
portolan add path/to/data/
portolan publish
```

## Submission Process

### 1. Fork this repository

Click the "Fork" button on GitHub to create your own copy.

### 2. Create a catalog entry

Create a new YAML file in the `catalogs/` directory:

```bash
# Use kebab-case for the filename
catalogs/my-catalog-name.yaml
```

Copy the template from `catalogs/placeholder.yaml` as a starting point.

### 3. Fill in all required fields

```yaml
# URL to your catalog root (must end with catalog.json)
url: https://example.com/path/to/catalog.json

# Human-readable title (3-100 characters)
title: My Catalog Title

# Brief description (10-500 characters)
description: A description of what this catalog contains and its data sources.

# Person or organization responsible for the catalog
maintainer: Your Name or Organization

# Contact email address
contact: your.email@example.com

# Access level: "public" (no auth) or "authenticated" (requires credentials)
access: public

# Tags for discovery (lowercase kebab-case, at least one required)
tags:
  - your-tag
  - another-tag

# Date of submission (YYYY-MM-DD format)
submitted: 2026-06-09

# Validation block (use these exact values for new submissions)
validation:
  tier: unvalidated
  last_checked: null
  stac_valid: null
  has_versions_json: null
  has_portolan_dir: null
```

### 4. Open a pull request

Push your changes and open a PR against the `main` branch.

## Field Reference

| Field | Required | Format | Description |
|-------|----------|--------|-------------|
| `url` | Yes | URI ending with `catalog.json` | URL to the STAC catalog root |
| `title` | Yes | 3-100 characters | Human-readable catalog name |
| `description` | Yes | 10-500 characters | Brief description of contents |
| `maintainer` | Yes | Non-empty string | Person or organization name |
| `contact` | Yes | Valid email | Contact email address |
| `access` | Yes | `public` or `authenticated` | Access level required |
| `tags` | Yes | Array of kebab-case strings | At least one tag for discovery |
| `submitted` | Yes | ISO 8601 date (YYYY-MM-DD) | Date of submission |
| `validation.tier` | Yes | `unvalidated`, `basic`, or `full` | Use `unvalidated` for new submissions |
| `validation.last_checked` | Yes | ISO 8601 datetime or `null` | Use `null` for new submissions |
| `validation.stac_valid` | Yes | boolean or `null` | Use `null` for new submissions |
| `validation.has_versions_json` | Yes | boolean or `null` | Use `null` for new submissions |
| `validation.has_portolan_dir` | Yes | boolean or `null` | Use `null` for new submissions |

### Tag Guidelines

- Use lowercase letters and hyphens only (kebab-case)
- Be specific: prefer `netherlands` over `europe`
- Include data type tags: `buildings`, `roads`, `land-cover`, `imagery`
- Include source tags: `osm`, `sentinel-2`, `landsat`

## Validation

When you open a PR, CI will automatically:

1. **Validate schema** - Ensure your YAML matches `schema/entry.schema.json`
2. **Check URL liveness** - Verify your catalog URL is accessible
3. **Validate STAC** - Check that the catalog is valid STAC (when implemented)

Fix any validation errors reported in the PR checks before requesting review.

## After Merge

Once your PR is merged:

1. Your catalog appears in `exports/catalogs.json`
2. The catalog will be visible in the web UI at [portolan.dev/registry](https://portolan.dev/registry) (coming soon)
3. Periodic validation will update the `validation` fields as checks run

## Questions?

Open an issue if you have questions or need help with your submission.
