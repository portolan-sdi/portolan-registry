# Registry contract changelog

This changelog lists each version of the registry contract: the extension
schema `extension.json` and the coverage schema `coverage.json`. It follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the versions
follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html). An
unreleased heading also names its version, such as `## [0.1.1] - Unreleased`.

For versioning rules and instructions to change or release a schema, see
the [schema guide](./README.md). The `Contract Version` check reads the first
`## [X.Y.Z]` heading in this file.

## [0.1.0] - 2026-10-30

### Added

- The extension schema, which defines registry fields on the root Catalog
  and each child link.
- The coverage schema, which defines the coverage index and a coverage
  file for each catalog.
- `portolan_registry:generated` and `portolan_registry:count` on the root.
  These fields replace `generated` and `count` in the raw export.
- `portolan_registry:coverage` on each child link.

### Changed

- `portolan_registry:licenses` now uses an array of `{id, count}` objects.
  The raw export uses an object with one key for each license.
