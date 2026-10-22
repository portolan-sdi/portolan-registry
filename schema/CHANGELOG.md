# Registry contract changelog

This file lists each version of the registry contract. The contract is the
extension schema `extension.json` and the coverage schema `coverage.json`.
The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the versions
follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

The [schema guide](./README.md) gives the rules for each version step, and
the steps to change a schema and to release a version. The `Contract Version`
check reads the top heading of this file.

## [0.1.0] - 2026-10-22

### Added

- The extension schema. It defines the registry fields on the root Catalog
  and on each child link.
- The coverage schema. It defines one coverage file for each catalog and
  the coverage index.
- `portolan_registry:generated` and `portolan_registry:count` on the root.
  They replace the bare `generated` and `count` fields of the raw export.
- `portolan_registry:coverage` on each child link.

### Changed

- `portolan_registry:licenses` is an array of `{id, count}` objects. The raw
  export uses an object with one key for each license.
