# Portolan Registry

A discovery layer for Portolan catalogs — STAC-based geospatial data catalogs with versioning and reproducibility.

## What is Portolan Registry?

Portolan Registry is a community-maintained index of [Portolan](https://github.com/portolan-sdi/portolan-spec)-compatible STAC catalogs. It provides a central place to discover, browse, and validate geospatial data catalogs.

## How It Works

1. **Submit a URL** — just the `catalog.json` link
2. **CI crawls the catalog** — extracts title, description, bbox, license, collection count, feature count
3. **Validation checks** — verifies STAC compliance, versions.json, .portolan/, llms.txt
4. **Export generated** — enriched metadata published to `exports/catalogs.json`

That's it. All metadata is auto-extracted from the catalog itself.

## Browse Catalogs

**Web UI**: [portolan-sdi.org](https://www.portolan-sdi.org)

**Direct access**: Browse entries in [`catalogs/`](./catalogs) or fetch the enriched export:
```
https://raw.githubusercontent.com/portolan-sdi/portolan-registry/main/exports/catalogs.json
```

## Submit a Catalog

### Prerequisites

Your catalog must be:
- A valid STAC Catalog (1.0.0+)
- Publicly accessible via HTTPS
- Ideally a full Portolan catalog with `.portolan/`, `versions.json`, and `llms.txt`

### Steps

1. **Fork this repository**

2. **Create a YAML file** in `catalogs/` named after your catalog (kebab-case):
   ```
   catalogs/my-awesome-catalog.yaml
   ```

3. **Add your catalog URL** — that's the only required field:
   ```yaml
   url: https://your-bucket.s3.amazonaws.com/path/to/catalog.json
   ```

4. **Open a Pull Request**

### What Happens Next

1. **CI validates your catalog**
   - Fetches the catalog.json
   - Crawls child catalogs and collections
   - Extracts metadata (title, description, bbox, license, etc.)
   - Checks for Portolan requirements (versions.json, .portolan/, llms.txt)

2. **If validation passes**, your PR can be merged

3. **On merge**, the publish workflow:
   - Re-crawls all catalogs
   - Generates `exports/catalogs.json` with full metadata
   - Commits the updated export

### Validation Checks

| Check | Description |
|-------|-------------|
| `stac_valid` | Catalog and collections are valid STAC JSON |
| `has_versions_json` | Collections have `rel: version-history` links |
| `has_portolan_dir` | Catalog has `.portolan/config.yaml` |
| `has_llms_txt` | Catalog has `rel: llms` link to llms.txt |

### Updating Your Catalog

Metadata is re-extracted on every publish. To trigger a refresh:
- Make any change to your YAML file (even a comment)
- Or manually trigger the publish workflow

## Export Schema

The generated `exports/catalogs.json` contains:

```json
{
  "generated": "2026-06-09T10:00:00Z",
  "count": 1,
  "catalogs": [
    {
      "id": "portolan-nl",
      "url": "https://data.source.coop/cholmes/portolan-nl/catalog.json",
      "title": "Portolan NL — Cloud-Native Dutch Geodata",
      "description": "Open geodatasets from Dutch government...",
      "bbox": [3.26, 50.73, 7.24, 53.55],
      "license": "CC0-1.0",
      "stac_version": "1.1.0",
      "providers": [...],
      "keywords": [...],
      "collection_count": 12,
      "feature_count": 15000000,
      "last_crawled": "2026-06-09T10:00:00Z",
      "validation": {
        "stac_valid": true,
        "has_versions_json": true,
        "has_portolan_dir": true,
        "has_llms_txt": true
      }
    }
  ]
}
```

## Related Projects

- [portolan-cli](https://github.com/portolan-sdi/portolan-cli) — Create and manage Portolan catalogs
- [portolan-spec](https://github.com/portolan-sdi/portolan-spec) — Portolan specification

## License

Apache License 2.0. See [LICENSE](./LICENSE).
