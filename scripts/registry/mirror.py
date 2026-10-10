"""A local copy of a catalog's metadata tree, for the validators to read.

rashid and stac-node-validator both validate a directory, not a URL. The
crawl already fetches every catalog and collection document, so
`MirroringFetcher` writes each one to disk as the crawl reads it.

The crawl alone does not make a tree the validators can judge. It counts item
links and never fetches the items, and it never reads AGENTS.md or README.md.
rashid resolves every structural link against the file tree and requires both
documents next to each catalog and collection. A mirror without them fails
every catalog that has items or documents. So `complete_mirror` fetches what
the crawl skipped: the targets of `child` and `item` links, and the two
documents beside each catalog and collection. It fetches metadata only. Asset
bytes stay out of the gate.

Each file goes to the path its URL has relative to the root catalog's
directory, so a relative href in a mirrored document resolves to the mirrored
target. A URL outside that directory has no place in the tree and is not
mirrored.
"""

from __future__ import annotations

import json
import posixpath
import threading
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

import requests

from registry.fetch import Fetcher, NotFound, resolve_url
from registry.report import log

# The relations whose targets rashid resolves against the file tree and the
# crawl may not have fetched. root, parent, and collection point back up the
# tree at documents the crawl has already read.
FOLLOWED_RELS = ("child", "item")

# PORTO-CORE-061 and PORTO-CORE-062: every catalog and collection directory
# holds both. rashid checks that each file exists and reads the README.
SIDECARS = ("AGENTS.md", "README.md")

# Items outnumber collections by orders of magnitude. ghsl links 3,984 of
# them, which takes about 40 seconds at this width.
WORKERS = 8

# The most documents one mirror fetches. The largest registered catalog,
# jrc-glofas, linked 4,336 items in October 2026. A server that makes up a
# new child URL on each request would otherwise hold the job until it times out.
MAX_DOCUMENTS = 50_000


class MirrorFull(requests.RequestException):
    """The mirror reached its document limit, so the URL was not fetched."""


class MirrorSource(Fetcher, Protocol):
    """A Fetcher that can also return a document's raw bytes."""

    def get_bytes(self, url: str, timeout: float = 30) -> bytes:
        """Fetch a document. Raises NotFound on 404 or 410."""
        ...


def _is_container(doc: Any) -> bool:
    return isinstance(doc, dict) and doc.get("type") in ("Catalog", "Collection")


class Mirror:
    """Files written under `dest`, and what happened to each URL."""

    def __init__(self, root_url: str, dest: Path, *, max_documents: int = MAX_DOCUMENTS) -> None:
        parts = urlsplit(root_url)
        self._origin = (parts.scheme, parts.netloc)
        self._base = posixpath.dirname(parts.path).rstrip("/") + "/"
        self.dest = dest
        self.max_documents = max_documents
        # True when the mirror stopped at `max_documents`. The tree on disk
        # then has holes, so no validator verdict on it means anything.
        self.truncated = False
        # "<url>: <error>" for each document that fetched and could not be
        # written. Two hrefs can need the same path as a file and as a
        # directory, or a name can be too long for the file system. The
        # mirror then differs from the catalog, which is not the catalog's
        # fault and is not a finding about it.
        self.unwritable: list[str] = []
        # URL -> parsed document, for every catalog and collection mirrored.
        self.containers: dict[str, dict[str, Any]] = {}
        # Every URL a fetch was started for, whether or not it succeeded.
        self.attempted: set[str] = set()
        # "<url>: <error>" for each fetch that failed for a reason other than
        # the file not being there. A missing file is rashid's finding to
        # report. A server error is not a fact about the catalog.
        self.failures: list[str] = []
        # Linked URLs outside the root catalog's directory, left unmirrored.
        self.outside: list[str] = []

    def path_for(self, url: str) -> Path | None:
        """Where `url` lives in the mirror, or None if outside the tree."""
        parts = urlsplit(url)
        if (parts.scheme, parts.netloc) != self._origin:
            return None
        # normpath folds every "..", so a path that still starts with the
        # base cannot climb out of `dest`.
        path = posixpath.normpath(parts.path)
        if not path.startswith(self._base):
            return None
        rel = path[len(self._base) :]
        if not rel or rel.startswith("/"):
            return None
        return self.dest / rel

    def write(self, url: str, data: bytes) -> bool:
        """Write `data` at the mirror path for `url`. False if outside.

        A write that fails goes to `unwritable` and does not raise.
        """
        path = self.path_for(url)
        if path is None:
            return False
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        except OSError as e:
            self.unwritable.append(f"{url}: {e}")
        return True

    def full(self) -> bool:
        """True when the mirror holds no room for another fetch."""
        if len(self.attempted) >= self.max_documents:
            self.truncated = True
        return self.truncated

    def problem(self) -> str | None:
        """Why the mirror cannot stand in for the catalog, or None."""
        if self.truncated:
            return (
                f"the catalog links more than {self.max_documents} documents, "
                "which is more than the registry mirrors"
            )
        if self.unwritable:
            return (
                f"{len(self.unwritable)} document(s) could not be written to the "
                f"mirror, first {self.unwritable[0]}"
            )
        return None

    def add_document(self, url: str, doc: Any) -> None:
        """Write a parsed document the crawl already holds."""
        if not self.write(url, json.dumps(doc, indent=2).encode("utf-8")):
            self.outside.append(url)
            return
        if _is_container(doc):
            self.containers[url] = doc


class MirroringFetcher:
    """Wraps a Fetcher and writes every JSON document it returns to a Mirror.

    The crawl decides what to fetch, so the mirror costs it no requests of
    its own.
    """

    def __init__(self, inner: Fetcher, mirror: Mirror) -> None:
        self._inner = inner
        self.mirror = mirror

    def get_json(self, url: str, timeout: float = 30) -> Any:
        if self.mirror.full():
            raise MirrorFull(f"{url}: not fetched, the mirror is full")
        self.mirror.attempted.add(url)
        doc = self._inner.get_json(url, timeout)
        self.mirror.add_document(url, doc)
        return doc

    def probe(self, url: str, timeout: float = 5) -> bool:
        return self._inner.probe(url, timeout)

    def head(self, url: str, timeout: float = 5) -> Mapping[str, str] | None:
        return self._inner.head(url, timeout)


def _wanted(mirror: Mirror, url: str, doc: dict[str, Any]) -> list[str]:
    """URLs this container needs in the mirror that nothing has fetched."""
    directory = url.rsplit("/", 1)[0]
    wanted = [f"{directory}/{name}" for name in SIDECARS]
    for link in doc.get("links") or []:
        if not isinstance(link, dict):
            continue
        href = link.get("href")
        if link.get("rel") in FOLLOWED_RELS and isinstance(href, str) and href:
            wanted.append(resolve_url(url, href))
    return wanted


def complete_mirror(
    mirror: Mirror,
    source_factory: Callable[[], MirrorSource],
    *,
    workers: int = WORKERS,
) -> None:
    """Fetch what the validators need and the crawl did not read.

    Runs in waves. Each wave fetches the items, children, and documents of
    the containers found so far. A child the crawl never reached, such as a
    collection nested in a collection, joins the next wave.

    `source_factory` builds one source per worker thread, because HttpFetcher
    makes no thread-safety promise for a shared session.
    """
    local = threading.local()

    def fetch(url: str) -> tuple[str, bytes | Exception]:
        if not hasattr(local, "source"):
            local.source = source_factory()
        try:
            return url, local.source.get_bytes(url)
        except Exception as e:
            return url, e

    pending = dict(mirror.containers)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        while pending:
            wave: list[str] = []
            for url, doc in pending.items():
                if mirror.truncated:
                    break
                for target in _wanted(mirror, url, doc):
                    if target in mirror.attempted:
                        continue
                    if mirror.full():
                        break
                    mirror.attempted.add(target)
                    if mirror.path_for(target) is None:
                        mirror.outside.append(target)
                        continue
                    wave.append(target)

            pending = {}
            for url, data in pool.map(fetch, wave):
                if isinstance(data, NotFound):
                    continue
                if isinstance(data, Exception):
                    mirror.failures.append(f"{url}: {data}")
                    log(f"  Warning: Failed to fetch {url}: {data}")
                    continue
                mirror.write(url, data)
                # Written as served. A document that does not parse stays in
                # the mirror, so rashid reports it as broken rather than as
                # missing.
                try:
                    doc = json.loads(data)
                except ValueError:
                    continue
                if _is_container(doc):
                    mirror.containers[url] = doc
                    pending[url] = doc
