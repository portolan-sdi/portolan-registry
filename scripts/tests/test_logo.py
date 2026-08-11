"""Reading a catalog's logo off its icon link.

The two shapes here are taken from live catalogs: `trimet` publishes a relative
href beside its catalog, `portolan-nl` an absolute one on another host.
"""

from __future__ import annotations

from conftest import FakeFetcher
from registry.logo import catalog_logo, find_logo_link

ROOT = "https://ex.org/catalog.json"
PNG = {"Content-Type": "image/png"}


def catalog(*links: dict) -> dict:
    return {"type": "Catalog", "id": "x", "links": list(links)}


def icon(**kw) -> dict:
    return {"rel": "icon", "href": "./logo.png", "type": "image/png", **kw}


class TestFindLogoLink:
    def test_finds_an_icon(self):
        link = icon(title="Example")
        assert find_logo_link(catalog(link)) == link

    def test_falls_back_to_preview(self):
        link = {"rel": "preview", "href": "./p.png", "type": "image/png"}
        assert find_logo_link(catalog(link)) == link

    def test_prefers_icon_over_preview(self):
        """Both rels carry an image, but only `icon` means the catalog's own
        branding. `preview` may be a thumbnail of the data."""
        preview = {"rel": "preview", "href": "./p.png", "type": "image/png"}
        assert find_logo_link(catalog(preview, icon()))["rel"] == "icon"

    def test_ignores_a_catalog_with_no_icon(self):
        assert find_logo_link(catalog({"rel": "child", "href": "./c.json"})) is None

    def test_ignores_an_undeclared_media_type(self):
        """stac-js drops an icon whose type it cannot recognise, so an
        undeclared one would not render for the clients that read these."""
        assert find_logo_link(catalog({"rel": "icon", "href": "./logo.png"})) is None

    def test_ignores_a_type_no_browser_displays(self):
        assert find_logo_link(catalog(icon(type="image/tiff"))) is None

    def test_ignores_an_empty_href(self):
        assert find_logo_link(catalog(icon(href=""))) is None

    def test_tolerates_a_catalog_with_no_links(self):
        assert find_logo_link({"type": "Catalog"}) is None

    def test_tolerates_links_that_are_not_a_list(self):
        assert find_logo_link({"links": "nonsense"}) is None

    def test_tolerates_a_link_that_is_not_an_object(self):
        assert find_logo_link({"links": ["nonsense", icon()]})["rel"] == "icon"


class TestCatalogLogo:
    def test_resolves_a_relative_href(self):
        """`trimet` points at a file beside its catalog. A consumer of the
        export has no way to resolve that itself."""
        f = FakeFetcher(heads={"https://ex.org/logo.png": PNG})
        logo = catalog_logo(catalog(icon(title="Example")), ROOT, f)
        assert logo == {
            "href": "https://ex.org/logo.png",
            "type": "image/png",
            "title": "Example",
        }

    def test_passes_an_absolute_href_through(self):
        """`portolan-nl` hosts its logo on GitHub Pages, away from the data."""
        href = "https://other.org/rijksoverheid.png"
        f = FakeFetcher(heads={href: PNG})
        logo = catalog_logo(catalog(icon(href=href)), ROOT, f)
        assert logo["href"] == href

    def test_omits_a_missing_title(self):
        f = FakeFetcher(heads={"https://ex.org/logo.png": PNG})
        assert "title" not in catalog_logo(catalog(icon()), ROOT, f)

    def test_reports_none_when_the_image_is_gone(self):
        """A dead href renders as a broken image, which is worse than none."""
        assert catalog_logo(catalog(icon()), ROOT, FakeFetcher()) is None

    def test_reports_none_when_the_host_answers_with_a_page(self):
        """Some hosts serve an HTML error page at 200 rather than a 404.
        Reachability alone would take that for an image."""
        f = FakeFetcher(heads={"https://ex.org/logo.png": {"Content-Type": "text/html"}})
        assert catalog_logo(catalog(icon()), ROOT, f) is None

    def test_reads_a_content_type_with_parameters(self):
        f = FakeFetcher(
            heads={"https://ex.org/logo.png": {"Content-Type": "image/png; charset=binary"}}
        )
        assert catalog_logo(catalog(icon()), ROOT, f)["type"] == "image/png"

    def test_tolerates_a_response_with_no_content_type(self):
        f = FakeFetcher(heads={"https://ex.org/logo.png": {}})
        assert catalog_logo(catalog(icon()), ROOT, f) is None

    def test_reports_none_without_asking_when_there_is_no_icon(self):
        f = FakeFetcher()
        assert catalog_logo(catalog(), ROOT, f) is None
        assert f.calls == []
