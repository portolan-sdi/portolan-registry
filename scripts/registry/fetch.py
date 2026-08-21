"""HTTP access for the crawler.

The entire network surface of the crawler is the three methods on `Fetcher`.
Keeping it that small is what makes `crawl_catalog` testable: tests pass a
fake and assert on crawl decisions rather than on HTTP transactions.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol
from urllib.parse import urljoin

import requests

# source.coop returns 403 to the default urllib User-Agent. `requests` sends
# its own and currently succeeds, but relying on that is luck. Identify
# ourselves so a host adding UA filtering does not take the nightly down.
USER_AGENT = "portolan-registry/1.0 (+https://github.com/portolan-sdi/portolan-registry)"


class Fetcher(Protocol):
    """Everything the crawler needs from the network."""

    def get_json(self, url: str, timeout: float = 30) -> Any:
        """Fetch and parse JSON. Raises on any non-2xx or transport error."""
        ...

    def probe(self, url: str, timeout: float = 5) -> bool:
        """Report whether `url` answers 200. Never raises."""
        ...

    def head(self, url: str, timeout: float = 5) -> Mapping[str, str] | None:
        """Response headers for a HEAD, or None if the URL does not answer 200.

        `probe` reports reachability alone, which cannot tell a logo apart from
        the HTML error page some hosts serve at 200 in place of a 404. Reading
        the headers lets a caller check what came back as well as that
        something did. Never raises.
        """
        ...


class HttpFetcher:
    """`Fetcher` backed by `requests`.

    Not thread-safe when constructed with a shared `Session`: `requests` makes
    no such guarantee. The publish entrypoint crawls with a thread pool, so
    construct one `HttpFetcher` per worker rather than sharing a session.
    """

    def __init__(
        self,
        session: requests.Session | None = None,
        user_agent: str = USER_AGENT,
    ) -> None:
        self._session = session or requests.Session()
        self._session.headers["User-Agent"] = user_agent

    def get_json(self, url: str, timeout: float = 30) -> Any:
        resp = self._session.get(url, timeout=timeout)
        resp.raise_for_status()
        return resp.json()

    def probe(self, url: str, timeout: float = 5) -> bool:
        try:
            resp = self._session.get(url, timeout=timeout)
        except requests.RequestException:
            return False
        return resp.status_code == 200

    def head(self, url: str, timeout: float = 5) -> Mapping[str, str] | None:
        try:
            resp = self._session.head(url, timeout=timeout, allow_redirects=True)
        except requests.RequestException:
            return None
        if resp.status_code != 200:
            return None
        return resp.headers


def resolve_url(base_url: str, href: str) -> str:
    """Resolve a link href against the URL it was found in.

    Portolan requires structural links to be relative and forbids a `self`
    link, so every child href must be resolved against its parent.
    """
    if href.startswith("http"):
        return href
    return urljoin(base_url, href)
