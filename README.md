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

**Web UI**: Coming soon at [portolan.dev/registry](https://portolan.dev/registry)

**Direct access**: Browse entries in [`catalogs/`](./catalogs) or fetch the enriched export:
```
https://raw.githubusercontent.com/portolan-sdi/portolan-registry/main/exports/catalogs.json
```

## Submit a Catalog

1. Fork this repo
2. Create `catalogs/your-catalog-name.yaml` with just:
   ```yaml
   url: https://your-bucket.s3.amazonaws.com/path/to/catalog.json
   ```
3. Open a PR

CI will validate the catalog is accessible and extract all metadata. See [CONTRIBUTING.md](./CONTRIBUTING.md) for details.

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
