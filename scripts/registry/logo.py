"""The logo a catalog shows next to its name in the registry.

Neither portolan-spec nor the STAC specification defines a catalog logo. STAC
best practices establishes only the mechanism, noting that a link "adds support
for thumbnails to STAC Catalogs as they can't list assets", and the `rel:
"icon"` spelling comes from stac-js and STAC Browser, which read icons off a
link and render them beside the title. Registered Portolan catalogs already
follow it, so the registry reads what they publish rather than inventing a
field of its own.

Two shapes appear in the wild. `trimet` points at a file beside its catalog
with a relative href, and `portolan-nl` points at an external host with an
absolute one. Both are conformant, because Portolan constrains only structural
links to be relative, so a reader has to resolve either.
"""

from __future__ import annotations

from collections.abc import Mapping

from registry.fetch import Fetcher, resolve_url

# Rels that carry an image for a catalog, best first. STAC best practices
# documents `preview` and stac-js implements `icon`; a catalog may reasonably
# use either, and reading both costs nothing.
LOGO_RELS = ("icon", "preview")

# Media types a browser renders in an <img>. stac-js excludes image/svg+xml
# from its own list, so STAC Browser will skip an SVG logo, but the registry
# publishes to a website where an SVG renders like any other image. Accepting
# it costs those consumers nothing and keeps a valid logo out of the bin.
DISPLAYABLE_TYPES = frozenset(
    {
        "image/apng",
        "image/avif",
        "image/gif",
        "image/jpeg",
        "image/png",
        "image/svg+xml",
        "image/webp",
    }
)


def find_logo_link(catalog: Mapping) -> Mapping | None:
    """The catalog's logo link, or None if it publishes no usable one.

    A link must declare a displayable image type to qualify. stac-js drops an
    icon whose media type it does not recognise, so an undeclared or exotic
    type would already fail to render for the consumers that read these links.
    """
    links = catalog.get("links")
    if not isinstance(links, list):
        return None
    for rel in LOGO_RELS:
        for link in links:
            if not isinstance(link, Mapping) or link.get("rel") != rel:
                continue
            href = link.get("href")
            media_type = link.get("type")
            if not isinstance(href, str) or not href:
                continue
            if media_type not in DISPLAYABLE_TYPES:
                continue
            return link
    return None


def catalog_logo(
    catalog: Mapping, base_url: str, fetcher: Fetcher
) -> dict[str, str] | None:
    """The logo to publish for a catalog, or None.

    The href is resolved against the catalog it was found in, so a consumer
    never has to know where the catalog lived. It is then fetched to confirm an
    image is really there: a logo that 404s renders as a broken image on the
    registry, which is worse than showing none. Some hosts answer a missing
    file with an HTML error page at 200, so the response has to say it is an
    image, not merely answer.

    A logo is decoration. Every failure here returns None and nothing else. It
    must never mark a catalog stale or fail its validation.
    """
    link = find_logo_link(catalog)
    if link is None:
        return None

    href = resolve_url(base_url, link["href"])
    headers = fetcher.head(href)
    if headers is None:
        return None
    content_type = str(headers.get("Content-Type", "")).split(";")[0].strip().lower()
    if not content_type.startswith("image/"):
        return None

    logo = {"href": href, "type": link["type"]}
    # The author's own words for the image, which the registry has no better
    # source for and a page needs as alt text.
    title = link.get("title")
    if isinstance(title, str) and title:
        logo["title"] = title
    return logo
