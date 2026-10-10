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
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# source.coop returns 403 to the default urllib User-Agent. `requests` sends
# its own and currently succeeds, but relying on that is luck. Identify
# ourselves so a host adding UA filtering does not take the nightly down.
USER_AGENT = "portolan-registry/1.0 (+https://github.com/portolan-sdi/portolan-registry)"

# source.coop answered the crawl with HTTP 520 in August 2026, and a second
# request a moment later succeeded. One transient error must not fail a
# submission or mark a catalog stale, so every GET and HEAD retries the
# statuses that mean "try again". 404 is not among them: a missing file stays
# missing.
RETRY_STATUSES = (429, 500, 502, 503, 504, 520, 521, 522, 523, 524)


def default_retry(backoff_factor: float = 1.0) -> Retry:
    """Three retries with exponential backoff, then the last response stands.

    `raise_on_status=False` hands the final response back instead of raising
    a urllib3 error, so `raise_for_status` reports it as a normal HTTPError.
    """
    return Retry(
        total=3,
        backoff_factor=backoff_factor,
        status_forcelist=RETRY_STATUSES,
        allowed_methods=frozenset({"GET", "HEAD"}),
        raise_on_status=False,
    )


class NotFound(requests.HTTPError):
    """The server answered 404 or 410: the document is not there.

    Kept apart from other HTTP errors because a missing file is a finding
    about the catalog, while a server error says nothing about it.
    """


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
        retry: Retry | None = None,
    ) -> None:
        self._session = session or requests.Session()
        self._session.headers["User-Agent"] = user_agent
        adapter = HTTPAdapter(max_retries=retry or default_retry())
        self._session.mount("https://", adapter)
        self._session.mount("http://", adapter)

    def _get(self, url: str, timeout: float) -> requests.Response:
        resp = self._session.get(url, timeout=timeout)
        if resp.status_code in (404, 410):
            raise NotFound(f"{resp.status_code} Not Found: {url}", response=resp)
        resp.raise_for_status()
        return resp

    def get_json(self, url: str, timeout: float = 30) -> Any:
        return self._get(url, timeout).json()

    def get_bytes(self, url: str, timeout: float = 30) -> bytes:
        """Fetch a document as bytes. Raises NotFound on 404 or 410.

        The mirror writes these bytes unchanged, so the validator reads the
        file the host serves, encoding and all.
        """
        return self._get(url, timeout).content

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
