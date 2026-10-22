# Registry contract changelog

This file records each version of the registry contract. The contract is the
extension schema `extension.json` and the coverage schema `coverage.json`.
Both schemas share one version number.

The top heading names the version that the next release publishes. While
that heading says `Unreleased`, the `next/` path serves the working schemas.
The `contract` check compares them with the latest release and fails when
the change needs a larger version step than the heading names.

Use these rules to choose the version:

| Change | Version step |
|---|---|
| A change to a description or documentation only | patch |
| A new optional or nullable field | minor |
| A removed or renamed field | major |
| A changed type, or a non-null field that becomes nullable | major |
| A changed unit or meaning | major |
| A new value in a closed enum such as `portolan_registry:status` | major |

The check cannot detect a changed unit or meaning. Name the major version
for that change yourself.

## [1.0.0] - Unreleased

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
