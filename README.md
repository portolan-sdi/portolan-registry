# Portolan Registry

A unified registry for [Portolan](https://github.com/portolan-sdi/portolan-spec) catalogs.

## How It Works

Submit a catalog URL → CI crawls & validates → metadata exported to [`exports/catalogs.json`](./exports/catalogs.json).

## Browse Catalogs

**Web**: [portolan-sdi.org](https://www.portolan-sdi.org) · **Raw**: [`catalogs/`](./catalogs)

## Submit a Catalog

**Web**: [portolan-sdi.org](https://www.portolan-sdi.org) — enter your `catalog.json` URL and an email address

**GitHub**: Fork, add `catalogs/your-catalog.yaml`, open PR:

```yaml
url: https://example.org/catalog.json
submitter_email: you@example.org
```

The address is how the registry reaches you when your catalog stops validating. It stays in `catalogs/`, and never appears in [`exports/catalogs.json`](./exports/catalogs.json).

See [Portolan spec](https://github.com/portolan-sdi/portolan-spec) for requirements.

## Schema

[`schema/entry.schema.json`](./schema/entry.schema.json)

## License

[Apache 2.0](./LICENSE)
