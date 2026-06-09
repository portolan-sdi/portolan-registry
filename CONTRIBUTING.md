# Contributing to Portolan Registry

## Submitting a Catalog

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

### Questions?

Open an issue or reach out to the maintainers.
