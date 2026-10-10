"""The validator wrappers and the verdict they reduce to.

Most tests replace the subprocess with a stub, so what is under test is how
the registry reads each tool's report. The tests in TestRealTools run the
pinned rashid and stac-node-validator, and skip when they are not installed
(`uv run --group validators`, `npm ci --prefix scripts/stac-node-validator`).
"""

from __future__ import annotations

import functools
import json
import shutil
import subprocess

import pytest
from conftest import FROZEN, FakeFetcher

from registry import validators
from registry.fetch import NotFound
from registry.mirror import Mirror
from registry.validators import (
    Finding,
    RuleGroup,
    ValidatorError,
    crawl_and_validate,
    group_findings,
    run_rashid,
    run_stac_node_validator,
    settle_stac_valid,
)

ROOT = "https://ex.org/catalog.json"


def stub(returncode=0, stdout="", stderr=""):
    calls = []

    def run(command, **kw):
        calls.append(command)
        return subprocess.CompletedProcess(command, returncode, stdout, stderr)

    run.calls = calls
    return run


def rashid_report(*findings):
    rules = {f["rule_id"] for f in findings}
    return json.dumps(
        {
            "passed": not any(f["severity"] == "error" for f in findings),
            "summary": {
                "by_rule": [{"rule_id": r, "description": f"{r} text"} for r in sorted(rules)]
            },
            "findings": list(findings),
        }
    )


def finding(rule_id="PTL-X-001", path="catalog.json", message="bad", tool="rashid"):
    return Finding(tool, rule_id, f"{rule_id} text", path, message)


class TestGroupFindings:
    def test_one_group_per_rule_with_a_count(self):
        groups = group_findings([finding(), finding(), finding("PTL-Y-002")])
        assert [(g.rule_id, g.count) for g in groups] == [("PTL-X-001", 2), ("PTL-Y-002", 1)]

    def test_keeps_three_examples(self):
        groups = group_findings([finding(path=f"c{i}.json") for i in range(10)])
        assert groups[0].examples == ["c0.json: bad", "c1.json: bad", "c2.json: bad"]
        assert groups[0].count == 10

    def test_the_same_rule_id_from_two_tools_stays_apart(self):
        groups = group_findings([finding(tool="rashid"), finding(tool="fetch")])
        assert len(groups) == 2

    def test_most_frequent_rule_first(self):
        groups = group_findings([finding("A")] + [finding("B")] * 3)
        assert [g.rule_id for g in groups] == ["B", "A"]


class TestRender:
    def test_names_the_tool_rule_and_count(self):
        text = RuleGroup("rashid", "PTL-FIL-001", "need AGENTS.md", 5, ["a: x"]).render()
        assert text.splitlines()[0] == "rashid PTL-FIL-001 (5 findings): need AGENTS.md"
        assert text.splitlines()[-1] == "  - and 4 more"

    def test_quotes_each_example_as_code(self):
        text = RuleGroup("rashid", "R", "d", 1, ["a.json: title `@someone`"]).render()
        assert text.splitlines()[1] == "  - `a.json: title '@someone'`"

    def test_a_blank_line_cannot_end_the_code_span(self):
        """A blank line closes a code span, and the mention after it renders."""
        example = "a.json: /assets/a\n\n@octocat ![x](https://e.org/x.png)"
        text = RuleGroup("stac-node-validator", "core", "d", 1, [example]).render()
        assert len(text.splitlines()) == 2
        assert text.splitlines()[1] == (
            "  - `a.json: /assets/a @octocat ![x](https://e.org/x.png)`"
        )

    def test_a_schema_url_rule_id_is_quoted(self):
        rule = "https://e.org/v1.0.0/schema.json\n\n@octocat"
        header = RuleGroup("stac-node-validator", rule, "d", 1, []).render().splitlines()[0]
        assert (
            header
            == "stac-node-validator `https://e.org/v1.0.0/schema.json @octocat` (1 finding): d"
        )


class TestRashid:
    def test_passes_the_flags_the_gate_needs(self, tmp_path):
        run = stub(stdout=rashid_report())
        run_rashid(tmp_path, run=run)
        command = run.calls[0]
        assert command[:2] == ["rashid", "check"]
        for flag in ("--json", "--no-data", "--schema", "--schema-allow-network"):
            assert flag in command
        assert "--data" not in command and "--live" not in command
        assert command[-1] == str(tmp_path)

    def test_keeps_errors_and_drops_warnings(self, tmp_path):
        report = rashid_report(
            {"rule_id": "PTL-A", "severity": "error", "path": "c.json", "message": "m"},
            {"rule_id": "PTL-B", "severity": "warning", "path": "c.json", "message": "w"},
        )
        found = run_rashid(tmp_path, run=stub(returncode=1, stdout=report))
        assert found == [Finding("rashid", "PTL-A", "PTL-A text", "c.json", "m")]

    def test_a_usage_error_is_not_a_verdict(self, tmp_path):
        with pytest.raises(ValidatorError, match="exited 2"):
            run_rashid(tmp_path, run=stub(returncode=2, stderr="No such option"))

    def test_output_that_is_not_json_is_not_a_verdict(self, tmp_path):
        with pytest.raises(ValidatorError, match="no JSON"):
            run_rashid(tmp_path, run=stub(returncode=1, stdout="Traceback"))

    def test_a_missing_binary_is_not_a_pass(self, tmp_path):
        def run(command, **kw):
            raise FileNotFoundError(command[0])

        with pytest.raises(ValidatorError, match="not installed"):
            run_rashid(tmp_path, run=run)


class TestStacNodeValidator:
    def test_reads_findings_by_schema(self, tmp_path):
        out = json.dumps(
            {
                "files_checked": 1,
                "findings": [{"path": "c.json", "schema": "core", "message": "/id must be string"}],
            }
        )
        found = run_stac_node_validator(tmp_path, run=stub(stdout=out))
        assert [(f.tool, f.rule_id, f.path) for f in found] == [
            ("stac-node-validator", "core", "c.json")
        ]

    def test_runs_the_committed_runner(self, tmp_path):
        run = stub(stdout='{"files_checked": 0, "findings": []}')
        run_stac_node_validator(tmp_path, run=run)
        assert run.calls[0][0] == "node"
        assert run.calls[0][1].endswith("stac-node-validator/report.cjs")

    def test_a_crash_is_not_a_verdict(self, tmp_path):
        with pytest.raises(ValidatorError, match="exited 1"):
            run_stac_node_validator(tmp_path, run=stub(returncode=1, stderr="boom"))

    def test_each_kind_of_schema_has_its_own_description(self, tmp_path):
        extension = "https://stac-extensions.github.io/file/v2.1.0/schema.json"
        out = json.dumps(
            {
                "files_checked": 1,
                "findings": [
                    {"path": "c.json", "schema": schema, "message": "m"}
                    for schema in ("core", "skipped", extension)
                ],
            }
        )
        found = run_stac_node_validator(tmp_path, run=stub(stdout=out))
        assert [f.description for f in found] == [
            "the STAC core schema rejects the object",
            "the object declares no STAC version that can be validated",
            "the extension schema rejects the object",
        ]


class TestValidateMirror:
    def test_runs_both_validators_on_the_mirror(self, tmp_path, monkeypatch):
        seen = []

        def fake(name):
            def run(mirror_dir):
                seen.append((name, mirror_dir))
                return [finding(name)]

            return run

        monkeypatch.setattr(validators, "run_rashid", fake("R"))
        monkeypatch.setattr(validators, "run_stac_node_validator", fake("S"))
        found = validators.validate_mirror(tmp_path)
        assert [f.rule_id for f in found] == ["R", "S"]
        assert seen == [("R", tmp_path), ("S", tmp_path)]


def catalog_tree():
    return FakeFetcher(
        docs={
            ROOT: {
                "type": "Catalog",
                "links": [{"rel": "child", "href": "./a/collection.json"}],
            },
            "https://ex.org/a/collection.json": {"type": "Collection", "links": []},
        }
    )


class TestCrawlAndValidate:
    def test_a_clean_report_is_valid(self):
        f = catalog_tree()
        result, report = crawl_and_validate(ROOT, lambda: f, now=FROZEN, validate=lambda d: [])
        assert report.passed is True
        assert result["validation"]["stac_valid"] is True

    def test_any_finding_is_invalid(self):
        f = catalog_tree()
        result, report = crawl_and_validate(
            ROOT, lambda: f, now=FROZEN, validate=lambda d: [finding()]
        )
        assert report.passed is False
        assert result["validation"]["stac_valid"] is False
        assert report.groups[0].rule_id == "PTL-X-001"

    def test_the_validators_read_the_mirror(self):
        seen = {}

        def validate(mirror_dir):
            seen["files"] = sorted(
                p.relative_to(mirror_dir).as_posix() for p in mirror_dir.rglob("*.json")
            )
            return []

        f = catalog_tree()
        crawl_and_validate(ROOT, lambda: f, now=FROZEN, validate=validate)
        assert seen["files"] == ["a/collection.json", "catalog.json"]

    def test_a_child_that_did_not_fetch_leaves_no_answer(self):
        """A slow host says nothing about the catalog. The gate still fails
        a new entry on the FETCH group, see test_validate_entries."""
        f = catalog_tree()
        f.docs["https://ex.org/a/collection.json"] = TimeoutError("timed out")
        result, report = crawl_and_validate(ROOT, lambda: f, now=FROZEN, validate=lambda d: [])
        assert report.passed is None
        assert result["validation"]["stac_valid"] is None
        assert [(g.tool, g.rule_id) for g in report.groups] == [("fetch", "FETCH")]
        assert "https://ex.org/a/collection.json" in report.groups[0].examples[0]

    def test_a_finding_beside_a_fetch_failure_is_invalid(self):
        f = catalog_tree()
        f.docs["https://ex.org/a/collection.json"] = TimeoutError("timed out")
        _, report = crawl_and_validate(ROOT, lambda: f, now=FROZEN, validate=lambda d: [finding()])
        assert report.passed is False

    def test_a_mirror_that_cannot_be_written_leaves_no_answer(self):
        """An item at ./a and another at ./a/item.json need `a` as a file and
        as a directory. The old code raised, and the nightly marked the
        catalog stale."""
        called = []
        f = FakeFetcher(
            docs={
                ROOT: {
                    "type": "Catalog",
                    "links": [
                        {"rel": "item", "href": "./a"},
                        {"rel": "item", "href": "./a/item.json"},
                    ],
                },
                "https://ex.org/a": {"type": "Feature", "id": "a"},
                "https://ex.org/a/item.json": {"type": "Feature", "id": "b"},
            }
        )
        result, report = crawl_and_validate(
            ROOT, lambda: f, now=FROZEN, validate=lambda d: called.append(d) or []
        )
        assert called == []
        assert report.passed is None
        assert "the mirror is incomplete" in report.error
        assert result["validation"]["stac_valid"] is None

    def test_a_mirror_at_its_limit_leaves_no_answer(self, monkeypatch):
        monkeypatch.setattr(validators, "Mirror", functools.partial(Mirror, max_documents=2))
        f = catalog_tree()
        f.docs["https://ex.org/a/collection.json"]["links"] = [
            {"rel": "item", "href": f"./i{n}.json"} for n in range(5)
        ]
        _, report = crawl_and_validate(ROOT, lambda: f, now=FROZEN, validate=lambda d: [])
        assert report.passed is None
        assert "more than 2 documents" in report.error

    def test_a_validator_that_cannot_run_leaves_no_answer(self):
        def validate(mirror_dir):
            raise ValidatorError("rashid is not installed")

        f = catalog_tree()
        result, report = crawl_and_validate(ROOT, lambda: f, now=FROZEN, validate=validate)
        assert report.passed is None
        assert report.error == "rashid is not installed"
        assert result["validation"]["stac_valid"] is None

    def test_an_unfetchable_root_still_raises(self):
        with pytest.raises(NotFound):
            crawl_and_validate(ROOT, FakeFetcher, now=FROZEN, validate=lambda d: [])

    def test_the_default_validator_is_looked_up_at_call_time(self, monkeypatch):
        monkeypatch.setattr(validators, "validate_mirror", lambda d: [finding("PATCHED")])
        f = catalog_tree()
        _, report = crawl_and_validate(ROOT, lambda: f, now=FROZEN)
        assert report.groups[0].rule_id == "PATCHED"


def answered(value):
    return {"validation": {"stac_valid": value}}


class TestSettleStacValid:
    LINK = {"href": ROOT, "portolan_registry:stac_valid": True}

    def test_no_answer_keeps_the_published_one(self):
        result = answered(None)
        settle_stac_valid(result, ROOT, self.LINK)
        assert result["validation"]["stac_valid"] is True

    def test_an_answer_replaces_the_published_one(self):
        result = answered(False)
        settle_stac_valid(result, ROOT, self.LINK)
        assert result["validation"]["stac_valid"] is False

    def test_a_new_url_does_not_inherit_the_old_answer(self):
        result = answered(None)
        settle_stac_valid(result, "https://other.org/catalog.json", self.LINK)
        assert result["validation"]["stac_valid"] is None

    def test_no_published_answer_stays_none(self):
        result = answered(None)
        settle_stac_valid(result, ROOT, {"href": ROOT, "portolan_registry:stac_valid": None})
        assert result["validation"]["stac_valid"] is None
        settle_stac_valid(result, ROOT, None)
        assert result["validation"]["stac_valid"] is None


TOOLS = (
    shutil.which("rashid")
    and shutil.which("node")
    and (validators.SNV_RUNNER.parent / "node_modules" / "stac-node-validator").is_dir()
)


class EmptyFetcher(FakeFetcher):
    """Answers `{}` for every URL: a root that is JSON and nothing more."""

    def get_json(self, url, timeout=30):
        self.calls.append(url)
        return {}

    def get_bytes(self, url, timeout=30):
        self.calls.append(f"BYTES {url}")
        return b"{}"


@pytest.mark.skipif(not TOOLS, reason="rashid and stac-node-validator are not installed")
class TestRealTools:
    def test_an_empty_root_fails(self):
        """Issue #200: a root that returns `{}` passed the old gate."""
        _, report = crawl_and_validate(ROOT, EmptyFetcher, now=FROZEN)
        assert report.error is None
        assert report.passed is False
        assert "rashid" in {g.tool for g in report.groups}
        assert "stac-node-validator" in {g.tool for g in report.groups}

    def test_plain_stac_without_the_profile_fails_on_conformance(self):
        f = FakeFetcher(
            docs={
                ROOT: {
                    "type": "Catalog",
                    "stac_version": "1.1.0",
                    "id": "plain",
                    "description": "Plain STAC.",
                    "links": [
                        {"rel": "root", "href": "./catalog.json", "type": "application/json"}
                    ],
                }
            }
        )
        _, report = crawl_and_validate(ROOT, lambda: f, now=FROZEN)
        assert report.error is None
        rules = {g.rule_id for g in report.groups if g.tool == "rashid"}
        assert "PTL-CNF-001" in rules
        # Valid STAC: stac-node-validator has nothing to say about it.
        assert not [g for g in report.groups if g.tool == "stac-node-validator"]
