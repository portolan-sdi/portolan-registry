# Registry contract changelog

This file records each version of the registry contract. The contract is the
extension schema `extension.json` and the coverage schema `coverage.json`.
Both schemas share one version number.

The top heading names the version that the next release publishes. While
that heading says `Unreleased`, the `next/` path serves the working schemas.
The `contract` check compares them with the latest release and fails when
the change needs a larger version step than the heading names.

The contract is pre-1.0. A consumer reads one release line, and a breaking
change starts a new line in a new path. Before 1.0.0, the minor version
names the line, so `v0.1/` holds every 0.1.x release. From 1.0.0 on, the
major version names the line, so `v1/` holds every 1.x.y release. Cargo
applies the same rule to 0.x versions.

Use these rules to choose the version:

| Change | Kind | Before 1.0.0 | From 1.0.0 on |
|---|---|---|---|
| A change to a description or documentation only | documentation | patch | patch |
| A new optional or nullable field | additive | patch | minor |
| A removed or renamed field | breaking | minor | major |
| A changed type, or a non-null field that becomes nullable | breaking | minor | major |
| A changed unit or meaning | breaking | minor | major |
| A new value in a closed enum such as `portolan_registry:status` | breaking | minor | major |

The check cannot detect a changed unit or meaning. Name the breaking version
for that change yourself.

After the first release of a new line, the registry writes the previous line
for 6 months. The release dates come from the headings of this file.

## [0.1.0] - Unreleased

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
