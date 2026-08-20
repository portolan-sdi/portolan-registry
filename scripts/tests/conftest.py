"""Shared test fixtures.

Crawler tests inject a FakeFetcher rather than mocking HTTP. What is worth
pinning down is which links the crawler follows and how it merges what it
finds, and encoding that as HTTP transactions puts plumbing between the test
and the assertion. Real HTTP behavior is covered in test_fetch.py.
"""

from __future__ import annotations

import json
import mimetypes
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

FIXTURES = Path(__file__).parent / "fixtures"

# Every timestamp in a golden comparison must be deterministic.
FROZEN = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)


@dataclass
class FakeFetcher:
    """In-memory Fetcher.

    `docs` maps URL -> parsed JSON. Store an Exception instance to simulate a
    timeout or malformed response; committed fixtures must stay valid JSON
    because CI runs json.tool over every .json file in the repo.
    """

    docs: dict[str, Any] = field(default_factory=dict)
    ok: set[str] = field(default_factory=set)
    calls: list[str] = field(default_factory=list)
    # URL -> response headers for `head`. A URL absent here does not answer.
    heads: dict[str, dict[str, str]] = field(default_factory=dict)

    def get_json(self, url: str, timeout: float = 30) -> Any:
        self.calls.append(url)
        if url not in self.docs:
            raise LookupError(f"404 Not Found: {url}")
        doc = self.docs[url]
        if isinstance(doc, Exception):
            raise doc
        return doc

    def probe(self, url: str, timeout: float = 5) -> bool:
        self.calls.append(f"GET {url}")
        return url in self.ok

    def head(self, url: str, timeout: float = 5) -> dict[str, str] | None:
        self.calls.append(f"HEAD {url}")
        return self.heads.get(url)


def load_tree(name: str) -> FakeFetcher:
    """Build a FakeFetcher from a fixture directory.

    Each `*.json` file under `fixtures/<name>/` is served at
    `https://ex.org/<relative path>`. Any other file is served to `head` alone,
    with a content type guessed from its name, which is all the crawler asks of
    an image.
    """
    root = FIXTURES / name
    docs = {}
    heads: dict[str, dict[str, str]] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        url = f"https://ex.org/{path.relative_to(root).as_posix()}"
        if path.suffix == ".json":
            docs[url] = json.loads(path.read_text())
            continue
        content_type, _ = mimetypes.guess_type(path.name)
        heads[url] = {"Content-Type": content_type or "application/octet-stream"}
    return FakeFetcher(docs=docs, heads=heads)


@pytest.fixture
def tree() -> FakeFetcher:
    """A nested catalog: root -> sub-catalog -> collections."""
    return load_tree("nested")


@pytest.fixture
def unmeasured() -> FakeFetcher:
    """A catalog nothing can be measured from: hidden items, unsized assets."""
    return load_tree("unmeasured")
