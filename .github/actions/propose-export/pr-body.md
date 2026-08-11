## What this changes

Republishes `exports/catalogs.json` from the latest crawl.

## Why

The export carries every catalog's counts, extent, and validation state, and it is the only place that state is stored. A crawl that never lands leaves the registry and the site serving stale data.

## Verification

`Validate Exports` checks this file against `schema/export.schema.json` on this pull request.

- [x] This change does not alter behavior
