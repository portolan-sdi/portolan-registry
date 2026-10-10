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
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

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


class MirrorSource(Fetcher, Protocol):
    """A Fetcher that can also return a document's raw bytes."""

    def get_bytes(self, url: str, timeout: float = 30) -> bytes:
        """Fetch a document. Raises NotFound on 404 or 410."""
        ...


def _is_container(doc: Any) -> bool:
    return isinstance(doc, dict) and doc.get("type") in ("Catalog", "Collection")


class Mirror:
    """Files written under `dest`, and what happened to each URL."""

    def __init__(self, root_url: str, dest: Path) -> None:
        parts = urlsplit(root_url)
        self._origin = (parts.scheme, parts.netloc)
        self._base = posixpath.dirname(parts.path).rstrip("/") + "/"
        self.dest = dest
        # URL -> parsed document, for every catalog and collection mirrored.
        self.containers: dict[str, dict] = {}
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
        """Write `data` at the mirror path for `url`. False if outside."""
        path = self.path_for(url)
        if path is None:
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return True

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
        self.mirror.attempted.add(url)
        doc = self._inner.get_json(url, timeout)
        self.mirror.add_document(url, doc)
        return doc

    def probe(self, url: str, timeout: float = 5) -> bool:
        return self._inner.probe(url, timeout)

    def head(self, url: str, timeout: float = 5):
        return self._inner.head(url, timeout)


def _wanted(mirror: Mirror, url: str, doc: dict) -> list[str]:
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

    def fetch(url: str) -> tuple[str, bytes | None, Exception | None]:
        if not hasattr(local, "source"):
            local.source = source_factory()
        try:
            return url, local.source.get_bytes(url), None
        except Exception as e:
            return url, None, e

    pending = dict(mirror.containers)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        while pending:
            wave: list[str] = []
            for url, doc in pending.items():
                for target in _wanted(mirror, url, doc):
                    if target in mirror.attempted:
                        continue
                    mirror.attempted.add(target)
                    if mirror.path_for(target) is None:
                        mirror.outside.append(target)
                        continue
                    wave.append(target)

            pending = {}
            for url, data, error in pool.map(fetch, wave):
                if isinstance(error, NotFound):
                    continue
                if error is not None:
                    mirror.failures.append(f"{url}: {error}")
                    log(f"  Warning: Failed to fetch {url}: {error}")
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
