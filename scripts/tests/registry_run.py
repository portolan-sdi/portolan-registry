"""Temporary registry for `publish_export` and `revalidate_all` tests.

Each test gets a working directory with `catalogs/` and a previous export at
`exports/catalogs.json`. The crawler reads the `nested` fixtures through
`FakeFetcher`. Tests configure the validator results.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from conftest import load_tree

from registry import validators
from registry.validators import Finding, ValidatorError

ROOT = "https://ex.org/catalog.json"


class Registry:
    def __init__(self, root: Path, monkeypatch, script) -> None:
        self.root = root
        self.catalogs = root / "catalogs"
        self.catalogs.mkdir()
        (root / "exports").mkdir()
        self.links: list[dict[str, Any]] = []
        self.findings: list[Finding] = []
        self.answers = True
        self.validators_answer = True
        monkeypatch.chdir(root)
        monkeypatch.setattr(script, "HttpFetcher", self.fetcher)
        monkeypatch.setattr(validators, "validate_mirror", self.validate)

    def validate(self, mirror_dir):
        if not self.validators_answer:
            raise ValidatorError("rashid timed out")
        return list(self.findings)

    def fetcher(self):
        tree = load_tree("nested")
        if not self.answers:
            tree.docs.pop(ROOT)
        return tree

    def fail_validation(self) -> None:
        self.findings.append(Finding("rashid", "PTL-CNF-001", "d", "catalog.json", "m"))

    def entry(self, catalog_id: str, extra: str = "") -> None:
        (self.catalogs / f"{catalog_id}.yaml").write_text(
            f"url: {ROOT}\nsubmitter_email: submitter@example.com\n{extra}"
        )

    def published(
        self,
        catalog_id: str,
        status: str = "valid",
        stale_days: int | None = None,
        stac_valid: bool | None = None,
    ):
        """Add a child link and its validation status to the previous export."""
        stale_since = None
        if stale_days is not None:
            stale_since = (datetime.now(UTC) - timedelta(days=stale_days)).isoformat()
        self.links.append(
            {
                "rel": "child",
                "href": ROOT,
                "type": "application/json",
                "title": catalog_id,
                "portolan_registry:id": catalog_id,
                "portolan_registry:status": status,
                "portolan_registry:collection_count": 0,
                "portolan_registry:last_validated": stale_since,
                "portolan_registry:stale_since": stale_since,
                "portolan_registry:failure_reason": "earlier failure" if stale_since else None,
                "portolan_registry:stac_valid": stac_valid,
                "portolan_registry:has_agents_md": False,
                "portolan_registry:has_readme": False,
            }
        )
        (self.root / "exports" / "catalogs.json").write_text(
            json.dumps({"type": "Catalog", "links": self.links})
        )

    def link(self, catalog_id: str) -> dict[str, Any]:
        export = json.loads((self.root / "exports" / "catalogs.json").read_text())
        return next(
            link for link in export["links"] if link.get("portolan_registry:id") == catalog_id
        )
