"""Real HTTP behavior of HttpFetcher.

The crawler tests inject a fake, so this is the only place the `requests`
usage is exercised. Kept small and offline via `responses`.
"""

from __future__ import annotations

import pytest
import requests
import responses

from registry.fetch import USER_AGENT, HttpFetcher, resolve_url

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
    def test_raises_on_server_error(self):
        responses.add(responses.GET, URL, status=520)
        with pytest.raises(requests.HTTPError):
            HttpFetcher().get_json(URL)

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
        responses.add(
            responses.GET, "https://ex.org/search", body=requests.ConnectTimeout()
        )
        assert HttpFetcher().probe("https://ex.org/search") is False

    @responses.activate
    def test_head_method_is_used_when_asked(self):
        responses.add(responses.HEAD, "https://ex.org/.portolan/config.yaml", status=200)
        assert (
            HttpFetcher().probe(
                "https://ex.org/.portolan/config.yaml", method="HEAD"
            )
            is True
        )


class TestResolveUrl:
    def test_absolute_href_is_returned_unchanged(self):
        assert resolve_url(URL, "https://other.org/x.json") == "https://other.org/x.json"

    def test_relative_href_resolves_against_the_parent(self):
        assert resolve_url(URL, "./sub/collection.json") == (
            "https://ex.org/sub/collection.json"
        )

    def test_parent_relative_href(self):
        assert resolve_url(
            "https://ex.org/a/b/catalog.json", "../c/collection.json"
        ) == "https://ex.org/a/c/collection.json"

    def test_bare_relative_href(self):
        assert resolve_url(URL, "sub/collection.json") == (
            "https://ex.org/sub/collection.json"
        )
