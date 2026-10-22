"""HTTP access for the crawler.

The entire network surface of the crawler is the three methods on `Fetcher`.
Keeping it that small is what makes `crawl_catalog` testable: tests pass a
fake and assert on crawl decisions rather than on HTTP transactions.
"""

from __future__ import annotations

import json
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

# The longest wait a `Retry-After` header can ask for. urllib3 allows 6 hours
# by default. The nightly crawls one catalog after another, so one host could
# then hold the whole job.
RETRY_AFTER_MAX_SECONDS = 30

# The largest document the registry reads. A STAC document is metadata. A
# larger body is not read into memory, and the fetch raises TooLarge.
MAX_DOCUMENT_BYTES = 32 * 1024 * 1024


def default_retry(backoff_factor: float = 1.0) -> Retry:
    """Three retries, then the last response stands.

    urllib3 sends the first retry with no delay. The second and third wait
    `backoff_factor` times 2 and 4 seconds. A `Retry-After` header replaces
    that wait, up to RETRY_AFTER_MAX_SECONDS.

    `raise_on_status=False` hands the final response back instead of raising
    a urllib3 error, so `raise_for_status` reports it as a normal HTTPError.
    """
    return Retry(
        total=3,
        backoff_factor=backoff_factor,
        status_forcelist=RETRY_STATUSES,
        allowed_methods=frozenset({"GET", "HEAD"}),
        raise_on_status=False,
        retry_after_max=RETRY_AFTER_MAX_SECONDS,
    )


MAX_ERROR_BYTES = 4096

S3_ACCESS_DENIED = b"<Code>AccessDenied</Code>"


class NotFound(requests.HTTPError):
    """The server answered 404 or 410: the document is not there.

    Kept apart from other HTTP errors because a missing file is a finding
    about the catalog, while a server error says nothing about it.

    An S3 bucket that denies `ListBucket` answers 403 AccessDenied for a key
    that does not exist. That answer raises NotFound too. The same 403 can
    mean that the key exists and is private. The validators cannot read a
    private file either, so the two cases get the same result.
    """


class TooLarge(requests.RequestException):
    """The body is larger than MAX_DOCUMENT_BYTES, so it was not read."""


class NotJson(ValueError):
    """The body does not parse as JSON. `content_type` names what came back."""

    def __init__(self, url: str, content_type: str, reason: str) -> None:
        self.content_type = content_type
        if "json" in content_type:
            message = f"{url} returns {content_type} that does not parse: {reason}"
        else:
            message = f"{url} returns {content_type}, not JSON"
        super().__init__(message)


def _s3_access_denied(resp: requests.Response) -> bool:
    """True when `resp` is the 403 AccessDenied that S3 sends for a missing key.

    The error code in the XML body identifies the answer. The
    `x-amz-request-id` header alone identifies S3 but not the cause of the 403.
    """
    if resp.status_code != 403:
        return False
    head = next(resp.iter_content(chunk_size=MAX_ERROR_BYTES), b"")
    return S3_ACCESS_DENIED in head


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

    def _get(self, url: str, timeout: float) -> tuple[bytes, str]:
        """The body and the content type of `url`.

        Stops at MAX_DOCUMENT_BYTES and raises TooLarge. The content type
        has no parameters, such as `text/html` for `text/html; charset=utf-8`.
        """
        with self._session.get(url, timeout=timeout, stream=True) as resp:
            if resp.status_code in (404, 410):
                raise NotFound(f"{resp.status_code} Not Found: {url}", response=resp)
            if _s3_access_denied(resp):
                raise NotFound(
                    f"403 AccessDenied from S3, so the file is missing or private: {url}",
                    response=resp,
                )
            resp.raise_for_status()
            content_type = resp.headers.get("Content-Type", "").split(";")[0].strip()
            body = bytearray()
            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                body.extend(chunk)
                if len(body) > MAX_DOCUMENT_BYTES:
                    raise TooLarge(
                        f"{url} is larger than {MAX_DOCUMENT_BYTES} bytes",
                        response=resp,
                    )
            return bytes(body), content_type or "no content type"

    def get_json(self, url: str, timeout: float = 30) -> Any:
        body, content_type = self._get(url, timeout)
        # json.loads detects UTF-8, UTF-16, and UTF-32 from the bytes.
        try:
            return json.loads(body)
        except ValueError as e:
            raise NotJson(url, content_type, str(e)) from e

    def get_bytes(self, url: str, timeout: float = 30) -> bytes:
        """Fetch a document as bytes. Raises NotFound as `NotFound` describes.

        The mirror writes these bytes unchanged, so the validator reads the
        file the host serves, encoding and all.
        """
        return self._get(url, timeout)[0]

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
