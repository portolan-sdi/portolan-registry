# Portolan Registry

A unified registry for [Portolan](https://github.com/portolan-sdi/portolan-spec) catalogs.

## How It Works

Submit a catalog URL → CI crawls & validates → metadata exported to [`exports/catalogs.json`](./exports/catalogs.json).

## Browse Catalogs

**Web**: [portolan-sdi.org](https://www.portolan-sdi.org) · **Raw**: [`catalogs/`](./catalogs)

## Submit a Catalog

**Web**: [portolan-sdi.org](https://www.portolan-sdi.org) — enter your `catalog.json` URL and an email address

Web submissions use the root catalog `id` as the registry ID and create `catalogs/<id>.yaml`. Each registry ID must be unique. CI also rejects a catalog URL that another entry uses.

**GitHub**: Fork, add `catalogs/your-catalog.yaml`, open PR:

```yaml
url: https://example.org/catalog.json
submitter_email: you@example.org
```

The address is how the registry reaches you when your catalog stops validating. It stays in `catalogs/`, and never appears in [`exports/catalogs.json`](./exports/catalogs.json).

See [Portolan spec](https://github.com/portolan-sdi/portolan-spec) for requirements.

## Show a Logo

The registry lists your catalog with its logo when your root `catalog.json` carries an `icon` link:

```json
{
  "rel": "icon",
  "href": "./_assets/your-logo.png",
  "type": "image/png",
  "title": "Your organisation"
}
```

The href may be relative to your catalog or absolute. `type` is required and must be one a browser renders: PNG, JPEG, GIF, WebP, AVIF, APNG, or SVG. `title` becomes the alt text.

The registry checks the image resolves before publishing it. A logo that does not is left out, and nothing else about your catalog changes.

## Report a Problem with a Catalog

Found a registered catalog serving bad data, a schema that contradicts itself, or an asset nobody can read? Open a [catalog feedback issue](https://github.com/portolan-sdi/portolan-registry/issues/new?template=catalog-feedback.yml). Name the catalog by its registry id, the stem of its file in [`catalogs/`](./catalogs), and paste the command you ran and what it printed.

Filing one mails whoever registered that catalog. The registry itself changes nothing: validation state comes from the nightly crawl, not from feedback.

## Schema

[`schema/entry.schema.json`](./schema/entry.schema.json)

## License

[Apache 2.0](./LICENSE)
