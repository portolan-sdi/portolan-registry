"""Run rashid and stac-node-validator over a mirror of a catalog.

The crawl reads a catalog. It does not judge it. This module mirrors the
metadata tree the crawl walks (registry.mirror), runs both validators on the
mirror, and reduces what they find to one group per rule.

rashid checks the Portolan profile and STAC 1.1.0 structure.
stac-node-validator checks the STAC core and extension schemas. The two
overlap little, so the gate runs both.

The flags were confirmed against rashid 0.1.8 in October 2026:

- `--no-data` skips the asset bytes. The registry declares about 1.25 TB of
  assets, and data checks belong in the publish tool.
- `--schema` adds the Portolan profile schema, which is off by default.
- `--schema-allow-network` fetches a profile schema the release does not
  bundle. 0.1.8 bundles v0.1.0 to v0.2.0, so this matters only for a catalog
  that declares a newer version.
- `--json` prints every finding, with no cap.
"""

from __future__ import annotations

import json
import re

# bandit B404: this module runs the pinned validators without a shell.
import subprocess  # nosec B404
import tempfile
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from registry.crawl import CrawlResult, crawl_catalog
from registry.entries import normalize_url
from registry.fetch import HttpFetcher
from registry.mirror import Mirror, MirroringFetcher, MirrorSource, complete_mirror
from registry.report import log

RASHID = ("rashid", "check", "--json", "--no-data", "--schema", "--schema-allow-network")

SNV_RUNNER = Path(__file__).resolve().parent.parent / "stac-node-validator" / "report.cjs"
SNV = ("node", str(SNV_RUNNER))

# The tool name of a finding that the mirror, not a validator, reports.
FETCH_TOOL = "fetch"

# Enough to show the shape of a failure. A flat list for ign-argentina held
# 1,547 lines in August 2026, which no pull request comment can carry.
EXAMPLES_PER_RULE = 3

# The longest a validator may run on one mirror. rashid takes about 16
# seconds on ghsl, which mirrors to 3,997 files.
TIMEOUT_SECONDS = 600

Runner = Callable[..., subprocess.CompletedProcess[str]]


class ValidatorError(Exception):
    """A validator could not run, so its silence proves nothing."""


@dataclass(frozen=True)
class Finding:
    tool: str
    rule_id: str
    description: str
    path: str
    message: str


# A rule id that renders as itself in Markdown, such as PTL-AST-001.
PLAIN_RULE_ID = re.compile(r"[A-Za-z0-9_.-]+")


def _code(text: str) -> str:
    """`text` as one Markdown code span on one line."""
    return "`" + " ".join(text.replace("`", "'").split()) + "`"


@dataclass
class RuleGroup:
    """Every finding of one rule, as a count and a few examples."""

    tool: str
    rule_id: str
    description: str
    count: int = 0
    examples: list[str] = field(default_factory=list)

    def render(self) -> str:
        """One Markdown list item, examples nested beneath it.

        The examples quote the catalog's own content into a pull request
        comment, so each goes in a code span. That keeps a mention or an
        image in a title from rendering. A blank line ends a code span, so
        each example becomes one line first. A stac-node-validator rule id
        is a schema URL from the catalog, so it goes in a code span too.
        """
        noun = "finding" if self.count == 1 else "findings"
        rule = self.rule_id if PLAIN_RULE_ID.fullmatch(self.rule_id) else _code(self.rule_id)
        lines = [f"{self.tool} {rule} ({self.count} {noun}): {self.description}"]
        for example in self.examples:
            lines.append(f"  - {_code(example)}")
        if self.count > len(self.examples):
            lines.append(f"  - and {self.count - len(self.examples)} more")
        return "\n".join(lines)


@dataclass
class ValidationReport:
    """The validators' verdict on one catalog.

    `error` is set when a validator could not run, or when the mirror is not
    a complete copy. `passed` is then None: the registry has no answer,
    which is a different claim from a pass.

    A FETCH group alone also gives None. A document that did not fetch after
    the retries says nothing about the catalog. A nightly run that counted it
    as a failure would flip `stac_valid` with each slow night. The gate still
    fails a new entry on a FETCH group, because it fails on every group.
    """

    groups: list[RuleGroup] = field(default_factory=list)
    error: str | None = None

    @property
    def passed(self) -> bool | None:
        if self.error is not None:
            return None
        if any(g.tool != FETCH_TOOL for g in self.groups):
            return False
        return None if self.groups else True


def group_findings(
    findings: Iterable[Finding], *, examples: int = EXAMPLES_PER_RULE
) -> list[RuleGroup]:
    """One group per (tool, rule), the most frequent first."""
    groups: dict[tuple[str, str], RuleGroup] = {}
    for f in findings:
        key = (f.tool, f.rule_id)
        group = groups.get(key)
        if group is None:
            group = groups[key] = RuleGroup(f.tool, f.rule_id, f.description)
        group.count += 1
        if len(group.examples) < examples:
            group.examples.append(f"{f.path}: {f.message}" if f.path else f.message)
    return sorted(groups.values(), key=lambda g: (-g.count, g.tool, g.rule_id))


def _run(command: list[str], run: Runner) -> subprocess.CompletedProcess[str]:
    try:
        return run(command, capture_output=True, text=True, timeout=TIMEOUT_SECONDS)
    except FileNotFoundError as e:
        raise ValidatorError(f"{command[0]} is not installed: {e}") from e
    except subprocess.TimeoutExpired as e:
        raise ValidatorError(f"{command[0]} ran longer than {TIMEOUT_SECONDS}s") from e


def _parse(name: str, proc: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    try:
        report: dict[str, Any] = json.loads(proc.stdout)
    except ValueError as e:
        detail = (proc.stderr or proc.stdout or "").strip()[-500:]
        raise ValidatorError(f"{name} printed no JSON report: {detail}") from e
    return report


def run_rashid(mirror_dir: Path, *, run: Runner = subprocess.run) -> list[Finding]:
    """Error-severity rashid findings. Warnings and infos do not gate."""
    proc = _run([*RASHID, str(mirror_dir)], run)
    # rashid exits 0 on a pass and 1 when it found an error. Anything else is
    # a usage error or a crash, and its report cannot be trusted.
    if proc.returncode not in (0, 1):
        detail = (proc.stderr or proc.stdout or "").strip()[-500:]
        raise ValidatorError(f"rashid exited {proc.returncode}: {detail}")
    report = _parse("rashid", proc)
    descriptions = {
        r["rule_id"]: r.get("description", "")
        for r in (report.get("summary") or {}).get("by_rule", [])
    }
    return [
        Finding(
            tool="rashid",
            rule_id=f["rule_id"],
            description=descriptions.get(f["rule_id"], ""),
            path=f.get("path", ""),
            message=f.get("message", ""),
        )
        for f in report.get("findings", [])
        if f.get("severity") == "error"
    ]


def _snv_description(schema: str) -> str:
    if schema == "core":
        return "the STAC core schema rejects the object"
    if schema == "skipped":
        return "the object declares no STAC version that can be validated"
    # The rule id names the schema. The description stays free of catalog text.
    return "the extension schema rejects the object"


def run_stac_node_validator(mirror_dir: Path, *, run: Runner = subprocess.run) -> list[Finding]:
    """Every stac-node-validator error, keyed by the schema that raised it."""
    proc = _run([*SNV, str(mirror_dir)], run)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[-500:]
        raise ValidatorError(f"stac-node-validator exited {proc.returncode}: {detail}")
    report = _parse("stac-node-validator", proc)
    return [
        Finding(
            tool="stac-node-validator",
            rule_id=f["schema"],
            description=_snv_description(f["schema"]),
            path=f.get("path", ""),
            message=f.get("message", ""),
        )
        for f in report.get("findings", [])
    ]


def validate_mirror(mirror_dir: Path) -> list[Finding]:
    """Run both validators. Raises ValidatorError if either cannot run."""
    return run_rashid(mirror_dir) + run_stac_node_validator(mirror_dir)


def _fetch_findings(failures: Iterable[str]) -> list[Finding]:
    return [
        Finding(
            tool=FETCH_TOOL,
            rule_id="FETCH",
            description="a linked document did not fetch, so nothing could check it",
            path="",
            message=failure,
        )
        for failure in failures
    ]


def settle_stac_valid(
    result: CrawlResult, url: str, previous_link: Mapping[str, Any] | None
) -> None:
    """Keep the last published `stac_valid` when this run has no answer.

    A run with no answer is a slow host or a broken tool, not news about the
    catalog. Without this, each such night would write and commit the export.
    A catalog with no published answer keeps None. So does a catalog whose
    entry now names another URL, because the old answer is about another
    catalog.
    """
    if result["validation"]["stac_valid"] is not None or not previous_link:
        return
    href = previous_link.get("href")
    if not href or normalize_url(href) != normalize_url(url):
        return
    previous = previous_link.get("portolan_registry:stac_valid")
    if isinstance(previous, bool):
        log(f"  Note: no validator answer this run, so stac_valid stays {previous}")
        result["validation"]["stac_valid"] = previous


def log_validation(report: ValidationReport) -> None:
    """Report what the validators found. Status does not depend on it yet."""
    if report.error:
        log(f"  Validators could not run: {report.error}")
    elif report.groups:
        rules = ", ".join(f"{g.tool} {g.rule_id} x{g.count}" for g in report.groups)
        log(f"  Validation failed: {rules}")
    else:
        log("  Validation passed")


def crawl_and_validate(
    url: str,
    source_factory: Callable[[], MirrorSource] = HttpFetcher,
    *,
    now: datetime | None = None,
    validate: Callable[[Path], list[Finding]] | None = None,
) -> tuple[CrawlResult, ValidationReport]:
    """Crawl `url`, mirror its metadata, and validate the mirror.

    Raises as crawl_catalog does when the root cannot be fetched. A validator
    that cannot run does not raise. It sets `report.error`, and `stac_valid`
    becomes None. A mirror that is not a complete copy does the same, and the
    validators do not run. The caller decides what an unanswered check means.
    The gate refuses the entry. The nightly keeps the last published answer
    (`settle_stac_valid`).

    Every child that did not fetch is a FETCH finding. The crawl skips past
    it, and a tree with a hole in it has not been validated.
    """
    validate = validate or validate_mirror
    with tempfile.TemporaryDirectory(prefix="portolan-mirror-") as tmp:
        mirror_dir = Path(tmp)
        mirror = Mirror(url, mirror_dir)
        result = crawl_catalog(url, MirroringFetcher(source_factory(), mirror), now=now)
        complete_mirror(mirror, source_factory)
        if mirror.outside:
            log(f"  Note: {len(mirror.outside)} linked URL(s) outside the tree, not mirrored")

        findings = _fetch_findings([*result["fetch_failures"], *mirror.failures])
        report = ValidationReport()
        problem = mirror.problem()
        if problem is not None:
            # A verdict on an incomplete copy says nothing about the catalog.
            report.error = f"the mirror is incomplete: {problem}"
        else:
            try:
                findings.extend(validate(mirror_dir))
            except ValidatorError as e:
                report.error = str(e)
        report.groups = group_findings(findings)

    result["validation"]["stac_valid"] = report.passed
    return result, report
