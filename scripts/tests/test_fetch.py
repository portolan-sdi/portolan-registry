"""Real HTTP behavior of HttpFetcher.

The crawler tests inject a fake, so this is the only place the `requests`
usage is exercised. Kept small and offline via `responses`.
"""

from __future__ import annotations

import pytest
import requests
import responses

from registry import fetch
from registry.fetch import (
    RETRY_AFTER_MAX_SECONDS,
    USER_AGENT,
    HttpFetcher,
    NotFound,
    TooLarge,
    default_retry,
    resolve_url,
)

URL = "https://ex.org/catalog.json"


class TestGetJson:
    @responses.activate
    def test_returns_parsed_json(self):
        responses.add(responses.GET, URL, json={"type": "Catalog"}, status=200)
        assert HttpFetcher().get_json(URL) == {"type": "Catalog"}

    @responses.activate
    def test_raises_on_404(self):
        responses.add(responses.GET, URL, status=404)
        with pytest.raises(requests.HTTPError):
            HttpFetcher().get_json(URL)

    @responses.activate
    def test_raises_not_found_on_404(self):
        responses.add(responses.GET, URL, status=404)
        with pytest.raises(NotFound):
            HttpFetcher().get_json(URL)

    @responses.activate
    def test_does_not_retry_a_404(self):
        responses.add(responses.GET, URL, status=404)
        with pytest.raises(NotFound):
            HttpFetcher(retry=default_retry(0)).get_json(URL)
        assert len(responses.calls) == 1

    @responses.activate
    def test_raises_on_server_error(self):
        responses.add(responses.GET, URL, status=520)
        with pytest.raises(requests.HTTPError) as caught:
            HttpFetcher(retry=default_retry(0)).get_json(URL)
        # A server error says nothing about whether the file exists.
        assert not isinstance(caught.value, NotFound)

    @responses.activate
    def test_retries_a_transient_server_error(self):
        """source.coop answered 520 in August 2026, then 200 on a retry."""
        responses.add(responses.GET, URL, status=520)
        responses.add(responses.GET, URL, json={"type": "Catalog"}, status=200)
        assert HttpFetcher(retry=default_retry(0)).get_json(URL) == {"type": "Catalog"}
        assert len(responses.calls) == 2

    @responses.activate
    def test_gives_up_after_three_retries(self):
        responses.add(responses.GET, URL, status=503)
        with pytest.raises(requests.HTTPError):
            HttpFetcher(retry=default_retry(0)).get_json(URL)
        assert len(responses.calls) == 4

    @responses.activate
    def test_sends_an_identifying_user_agent(self):
        # source.coop 403s the default urllib UA.
        responses.add(responses.GET, URL, json={}, status=200)
        HttpFetcher().get_json(URL)
        assert responses.calls[0].request.headers["User-Agent"] == USER_AGENT

    @responses.activate
    def test_custom_user_agent_is_honored(self):
        responses.add(responses.GET, URL, json={}, status=200)
        HttpFetcher(user_agent="custom/1.0").get_json(URL)
        assert responses.calls[0].request.headers["User-Agent"] == "custom/1.0"


class TestLimits:
    def test_a_retry_after_header_cannot_hold_the_run(self):
        """urllib3 waits up to 6 hours for a Retry-After by default."""
        assert default_retry().retry_after_max == RETRY_AFTER_MAX_SECONDS
        assert RETRY_AFTER_MAX_SECONDS <= 60

    @responses.activate
    def test_a_body_over_the_limit_is_not_read(self, monkeypatch):
        monkeypatch.setattr(fetch, "MAX_DOCUMENT_BYTES", 10)
        responses.add(responses.GET, URL, body=b"x" * 11, status=200)
        with pytest.raises(TooLarge):
            HttpFetcher().get_bytes(URL)

    @responses.activate
    def test_a_body_at_the_limit_is_read(self, monkeypatch):
        monkeypatch.setattr(fetch, "MAX_DOCUMENT_BYTES", 10)
        responses.add(responses.GET, URL, body=b"x" * 10, status=200)
        assert HttpFetcher().get_bytes(URL) == b"x" * 10

    @responses.activate
    def test_too_large_is_a_request_error(self, monkeypatch):
        """The crawl and the gate catch RequestException for a failed child."""
        monkeypatch.setattr(fetch, "MAX_DOCUMENT_BYTES", 10)
        responses.add(responses.GET, URL, json={"description": "x" * 20}, status=200)
        with pytest.raises(requests.RequestException):
            HttpFetcher().get_json(URL)


class TestGetBytes:
    @responses.activate
    def test_returns_the_body_unchanged(self):
        url = "https://ex.org/README.md"
        responses.add(responses.GET, url, body="# T\u00edtulo\n".encode(), status=200)
        assert HttpFetcher().get_bytes(url) == "# T\u00edtulo\n".encode()

    @responses.activate
    def test_raises_not_found_on_410(self):
        url = "https://ex.org/README.md"
        responses.add(responses.GET, url, status=410)
        with pytest.raises(NotFound):
            HttpFetcher().get_bytes(url)


class TestProbe:
    @responses.activate
    def test_true_on_200(self):
        responses.add(responses.GET, "https://ex.org/search", status=200)
        assert HttpFetcher().probe("https://ex.org/search") is True

    @responses.activate
    def test_false_on_404_without_raising(self):
        responses.add(responses.GET, "https://ex.org/search", status=404)
        assert HttpFetcher().probe("https://ex.org/search") is False

    @responses.activate
    def test_false_on_transport_error(self):
        responses.add(responses.GET, "https://ex.org/search", body=requests.ConnectTimeout())
        assert HttpFetcher().probe("https://ex.org/search") is False


class TestResolveUrl:
    def test_absolute_href_is_returned_unchanged(self):
        assert resolve_url(URL, "https://other.org/x.json") == "https://other.org/x.json"

    def test_relative_href_resolves_against_the_parent(self):
        assert resolve_url(URL, "./sub/collection.json") == ("https://ex.org/sub/collection.json")

    def test_parent_relative_href(self):
        assert (
            resolve_url("https://ex.org/a/b/catalog.json", "../c/collection.json")
            == "https://ex.org/a/c/collection.json"
        )

    def test_bare_relative_href(self):
        assert resolve_url(URL, "sub/collection.json") == ("https://ex.org/sub/collection.json")


class TestHead:
    LOGO = "https://ex.org/logo.png"

    @responses.activate
    def test_returns_headers_on_200(self):
        responses.add(
            responses.HEAD,
            self.LOGO,
            status=200,
            headers={"Content-Type": "image/png"},
        )
        assert HttpFetcher().head(self.LOGO)["Content-Type"] == "image/png"

    @responses.activate
    def test_none_on_404_without_raising(self):
        responses.add(responses.HEAD, self.LOGO, status=404)
        assert HttpFetcher().head(self.LOGO) is None

    @responses.activate
    def test_none_on_transport_error(self):
        responses.add(responses.HEAD, self.LOGO, body=requests.ConnectTimeout())
        assert HttpFetcher().head(self.LOGO) is None

    @responses.activate
    def test_follows_a_redirect(self):
        """A logo served from a CDN commonly answers with a 302 first."""
        responses.add(
            responses.HEAD,
            self.LOGO,
            status=302,
            headers={"Location": "https://cdn.ex.org/logo.png"},
        )
        responses.add(
            responses.HEAD,
            "https://cdn.ex.org/logo.png",
            status=200,
            headers={"Content-Type": "image/png"},
        )
        assert HttpFetcher().head(self.LOGO)["Content-Type"] == "image/png"
