# Portolan Registry

A unified registry for [Portolan](https://github.com/portolan-sdi/portolan-spec) catalogs.

## How It Works

Submit a catalog URL → CI crawls & validates → metadata exported to [`exports/catalogs.json`](./exports/catalogs.json).

## Browse Catalogs

**Web**: [portolan-sdi.org](https://www.portolan-sdi.org) · **Raw**: [`catalogs/`](./catalogs)

## Submit a Catalog

**Web**: [portolan-sdi.org](https://www.portolan-sdi.org) — enter your `catalog.json` URL

**GitHub**: Fork, add `catalogs/your-catalog.yaml` with `url: https://...catalog.json`, open PR

See [Portolan spec](https://github.com/portolan-sdi/portolan-spec) for requirements.

## Schema

[`schema/entry.schema.json`](./schema/entry.schema.json)

## License

[Apache 2.0](./LICENSE)
